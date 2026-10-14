# -*- coding: utf-8 -*-
"""订单持久化与幂等(SQLite)。

对齐官方契约(interface-contract/aipay-interface-contract.md):
  - out_trade_no 主键全局唯一: 同一订单不会被重复创建
  - trade_no UNIQUE: 同一笔支付宝交易不能给两个订单付款, 也不会被重复履约
  - proof_hash UNIQUE: 同一 Payment-Proof 只能消费一次(防重放)
  - pay_before: 首次进入履约前必须仍在有效期内
  - prepare_fulfillment 原子化: 校验订单 -> 只生成一次资源 -> 落 PENDING_CONFIRM,
    重复请求返回已保存结果, 不重复生成、不重复回执

状态机:
  order_status:  PENDING_PAYMENT -> PAID
  fulfill_status: UNFULFILLED -> PENDING_CONFIRM -> FULFILLED
"""
import os
import sqlite3
import threading
from datetime import datetime

_CONN_LOCK = threading.Lock()


class DuplicateSettlement(Exception):
    """同一 trade_no / proof 已被消费(防重放触发)。"""


class Orders:
    def __init__(self, db_path):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.db_path = db_path
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS orders(
                out_trade_no   TEXT PRIMARY KEY,
                resource_id    TEXT NOT NULL,
                amount         TEXT NOT NULL,
                currency       TEXT NOT NULL DEFAULT 'CNY',
                goods_name     TEXT NOT NULL DEFAULT '',
                pay_before     TEXT NOT NULL DEFAULT '',
                order_status   TEXT NOT NULL DEFAULT 'PENDING_PAYMENT',
                fulfill_status TEXT NOT NULL DEFAULT 'UNFULFILLED',
                trade_no       TEXT UNIQUE,
                proof_hash     TEXT UNIQUE,
                service_result TEXT,
                created_at     INTEGER NOT NULL,
                paid_at        INTEGER,
                fulfilled_at   INTEGER
            )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_orders_res ON orders(resource_id)")
            # 兼容旧库: 缺列时补上
            cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
            for name, ddl in (
                ("pay_before", "ALTER TABLE orders ADD COLUMN pay_before TEXT NOT NULL DEFAULT ''"),
                ("goods_name", "ALTER TABLE orders ADD COLUMN goods_name TEXT NOT NULL DEFAULT ''"),
                ("order_status", "ALTER TABLE orders ADD COLUMN order_status TEXT NOT NULL DEFAULT 'PENDING_PAYMENT'"),
                ("fulfill_status", "ALTER TABLE orders ADD COLUMN fulfill_status TEXT NOT NULL DEFAULT 'UNFULFILLED'"),
                ("service_result", "ALTER TABLE orders ADD COLUMN service_result TEXT"),
                ("proof_hash", "ALTER TABLE orders ADD COLUMN proof_hash TEXT"),
            ):
                if name not in cols:
                    c.execute(ddl)

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, out_trade_no, resource_id, amount, currency="CNY",
               pay_before="", goods_name=""):
        with _CONN_LOCK, self._conn() as c:
            c.execute(
                "INSERT INTO orders(out_trade_no, resource_id, amount, currency, "
                "goods_name, pay_before, created_at) VALUES(?,?,?,?,?,?,?)",
                (out_trade_no, resource_id, amount, currency, goods_name,
                 pay_before, int(datetime.now().timestamp())))
        return self.get(out_trade_no)

    def get(self, out_trade_no):
        if not out_trade_no:
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM orders WHERE out_trade_no=?",
                            (out_trade_no,)).fetchone()
        return dict(row) if row else None

    def get_by_trade_no(self, trade_no):
        """按支付宝交易号反查订单。

        官方 Payment-Proof 只含 payment_proof / trade_no, 不含 out_trade_no;
        买家侧 CLI 重试时未必回传商户订单号, 服务端需能按 trade_no 兜底定位。
        """
        if not trade_no:
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM orders WHERE trade_no=?", (trade_no,)).fetchone()
        return dict(row) if row else None

    def prepare_fulfillment(self, out_trade_no, trade_no, expected_amount,
                            expected_resource_id, create_resource, pay_before_ok,
                            proof_hash=None):
        """原子进入履约: 校验 -> 只生成一次资源 -> 落 PENDING_CONFIRM。

        返回 {'state': 'PENDING_CONFIRM'|'FULFILLED', 'service_result': ...};
        校验不通过返回 None。同一订单重复调用返回已保存结果, 绝不重复生成资源。
        """
        with _CONN_LOCK, self._conn() as c:
            row = c.execute("SELECT * FROM orders WHERE out_trade_no=?",
                            (out_trade_no,)).fetchone()
            if row is None:
                return None
            o = dict(row)
            if o["amount"] != expected_amount or o["resource_id"] != expected_resource_id:
                return None
            # 已生成过资源: 只重试确认或返回已保存结果, 不重复生成
            if o["fulfill_status"] in ("PENDING_CONFIRM", "FULFILLED"):
                return {"state": o["fulfill_status"],
                        "service_result": o["service_result"]}
            # 首次进入履约: 必须仍在 pay_before 内
            if o["fulfill_status"] == "UNFULFILLED" and not pay_before_ok:
                return None
            service_result = create_resource()
            try:
                c.execute(
                    "UPDATE orders SET order_status='PAID', fulfill_status='PENDING_CONFIRM', "
                    "trade_no=?, proof_hash=?, service_result=?, paid_at=? "
                    "WHERE out_trade_no=? AND fulfill_status='UNFULFILLED'",
                    (trade_no, proof_hash, service_result,
                     int(datetime.now().timestamp()), out_trade_no))
            except sqlite3.IntegrityError as e:
                raise DuplicateSettlement(f"交易号已被消费(防重复履约): {e}") from e
            if c.total_changes == 0:
                raise DuplicateSettlement("订单履约状态已变更, 放弃本次写入")
        return {"state": "PENDING_CONFIRM", "service_result": service_result}

    def mark_fulfilled(self, out_trade_no, trade_no):
        """履约确认成功后才标记最终完成。"""
        with _CONN_LOCK, self._conn() as c:
            c.execute("UPDATE orders SET fulfill_status='FULFILLED', fulfilled_at=? "
                      "WHERE out_trade_no=? AND trade_no=? AND fulfill_status='PENDING_CONFIRM'",
                      (int(datetime.now().timestamp()), out_trade_no, trade_no))
        return self.get(out_trade_no)

    def stats(self):
        with self._conn() as c:
            rows = c.execute(
                "SELECT fulfill_status, COUNT(*) n FROM orders GROUP BY fulfill_status").fetchall()
        return {r["fulfill_status"]: r["n"] for r in rows}
