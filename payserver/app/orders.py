"""订单持久化（SQLite，真实落盘，非内存）。

提供 A2M 流程所需的 create_pending / find_by_out_trade_no / prepare_fulfillment /
mark_fulfilled。prepare_fulfillment 原子地只生成一次授权码（幂等），避免重复履约。
"""
import os
import sqlite3
import threading
import time

from . import config

_lock = threading.Lock()
_COLS = [
    "out_trade_no", "amount", "currency", "resource_id", "goods_name",
    "pay_before", "machine_code", "order_status", "fulfill_status",
    "trade_no", "license_key", "created_at",
]


def _conn():
    os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders(
            out_trade_no TEXT PRIMARY KEY,
            amount TEXT, currency TEXT, resource_id TEXT, goods_name TEXT,
            pay_before TEXT, machine_code TEXT, order_status TEXT,
            fulfill_status TEXT, trade_no TEXT, license_key TEXT,
            created_at TEXT
        )"""
    )
    return conn


_conn()


def create_pending(o: dict):
    with _lock, _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO orders(
                out_trade_no, amount, currency, resource_id, goods_name,
                pay_before, machine_code, order_status, fulfill_status, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                o["out_trade_no"], o["amount"], o["currency"], o["resource_id"],
                o["goods_name"], o["pay_before"], o.get("machine_code", "*"),
                "PENDING_PAYMENT", "UNFULFILLED",
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def find_by_out_trade_no(no: str):
    with _conn() as c:
        row = c.execute(
            "SELECT " + ",".join(_COLS) + " FROM orders WHERE out_trade_no=?", (no,)
        ).fetchone()
    if not row:
        return None
    return dict(zip(_COLS, row))


def prepare_fulfillment(spec: dict):
    """原子准备履约：已处于 PENDING_CONFIRM/FULFILLED 直接返回既有授权码；
    否则调用 create_resource() 生成授权码并落盘为 PENDING_CONFIRM。"""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT fulfill_status, license_key FROM orders WHERE out_trade_no=?",
            (spec["out_trade_no"],),
        ).fetchone()
        if row and row[0] in ("PENDING_CONFIRM", "FULFILLED"):
            return {"state": row[0], "service_result": row[1]}
        license_key = spec["create_resource"]()
        c.execute(
            "UPDATE orders SET fulfill_status=?, trade_no=?, license_key=? WHERE out_trade_no=?",
            ("PENDING_CONFIRM", spec["trade_no"], license_key, spec["out_trade_no"]),
        )
        return {"state": "PENDING_CONFIRM", "service_result": license_key}


def mark_fulfilled(no: str, trade_no: str):
    with _lock, _conn() as c:
        c.execute(
            "UPDATE orders SET fulfill_status=?, trade_no=? WHERE out_trade_no=?",
            ("FULFILLED", trade_no, no),
        )


def find_all():
    """返回全部订单（用于云端同步/商户控制台导出）。不含敏感授权私钥，仅给 has_license 标记。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT " + ",".join(_COLS) + " FROM orders ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(zip(_COLS, r))
        d["has_license"] = bool(d.get("license_key") or d.get("fulfill_status") == "FULFILLED")
        d.pop("license_key", None)  # 不对外暴露签发的授权码原文
        result.append(d)
    return result
