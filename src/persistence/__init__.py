"""持久化模块 - 订单簿、订单历史、成交记录持久化"""

import json
import os
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict


class PersistenceManager:
    """持久化管理器 - 支持 JSON 快照和 SQLite 增量记录"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._db_path = os.path.join(data_dir, "trading.db")
        self._init_db()

    def _init_db(self):
        """初始化 SQLite 数据库"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            # 订单历史表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    price TEXT,
                    quantity INTEGER,
                    filled_qty INTEGER,
                    cancelled_qty INTEGER,
                    status TEXT,
                    order_type TEXT,
                    is_mock INTEGER,
                    create_time TEXT,
                    update_time TEXT,
                    queue_length_at_enter INTEGER,
                    queue_position_at_enter INTEGER,
                    leave_queue_time TEXT,
                    reject_reason TEXT
                )
            """)
            # 成交记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    price TEXT,
                    quantity INTEGER,
                    trade_time TEXT,
                    match_source TEXT,
                    trigger_trade_id TEXT,
                    fee TEXT,
                    net_amount TEXT
                )
            """)
            # 订单簿快照表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    snapshot_time TEXT,
                    best_bid TEXT,
                    best_ask TEXT,
                    spread TEXT,
                    bid_levels TEXT,
                    ask_levels TEXT,
                    total_bid_qty INTEGER,
                    total_ask_qty INTEGER
                )
            """)
            # 日终结算记录
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settlements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    settle_date TEXT,
                    symbol TEXT,
                    cash TEXT,
                    available_position INTEGER,
                    frozen_position INTEGER,
                    today_bought_position INTEGER,
                    total_fees TEXT,
                    trade_count INTEGER
                )
            """)
            conn.commit()

    # ─────────── 订单持久化 ───────────

    def save_order(self, order_dict: dict):
        """保存或更新订单"""
        qi = order_dict.get("queue_info", {})
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, symbol, side, price, quantity, filled_qty, cancelled_qty,
                    status, order_type, is_mock, create_time, update_time,
                    queue_length_at_enter, queue_position_at_enter, leave_queue_time, reject_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_dict.get("order_id"),
                order_dict.get("symbol"),
                order_dict.get("side"),
                order_dict.get("price"),
                order_dict.get("quantity", 0),
                order_dict.get("filled_qty", 0),
                order_dict.get("cancelled_qty", 0),
                order_dict.get("status"),
                order_dict.get("order_type"),
                1 if order_dict.get("is_mock") else 0,
                order_dict.get("create_time"),
                order_dict.get("update_time"),
                qi.get("queue_length_at_enter") if qi else None,
                qi.get("queue_position_at_enter") if qi else None,
                qi.get("leave_queue_time") if qi else None,
                order_dict.get("reject_reason"),
            ))
            conn.commit()

    def save_orders_batch(self, orders: List[dict]):
        """批量保存订单"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            for order_dict in orders:
                qi = order_dict.get("queue_info", {})
                cursor.execute("""
                    INSERT OR REPLACE INTO orders (
                        order_id, symbol, side, price, quantity, filled_qty, cancelled_qty,
                        status, order_type, is_mock, create_time, update_time,
                        queue_length_at_enter, queue_position_at_enter, leave_queue_time, reject_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_dict.get("order_id"),
                    order_dict.get("symbol"),
                    order_dict.get("side"),
                    order_dict.get("price"),
                    order_dict.get("quantity", 0),
                    order_dict.get("filled_qty", 0),
                    order_dict.get("cancelled_qty", 0),
                    order_dict.get("status"),
                    order_dict.get("order_type"),
                    1 if order_dict.get("is_mock") else 0,
                    order_dict.get("create_time"),
                    order_dict.get("update_time"),
                    qi.get("queue_length_at_enter") if qi else None,
                    qi.get("queue_position_at_enter") if qi else None,
                    qi.get("leave_queue_time") if qi else None,
                    order_dict.get("reject_reason"),
                ))
            conn.commit()

    def get_orders(self, symbol: Optional[str] = None, status: Optional[str] = None,
                   limit: int = 1000) -> List[dict]:
        """查询订单历史"""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM orders WHERE 1=1"
            params = []
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY create_time DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    # ─────────── 成交记录持久化 ───────────

    def save_trade(self, trade_dict: dict):
        """保存成交记录"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, order_id, symbol, side, price, quantity,
                    trade_time, match_source, trigger_trade_id, fee, net_amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_dict.get("trade_id"),
                trade_dict.get("order_id"),
                trade_dict.get("symbol"),
                trade_dict.get("side"),
                trade_dict.get("price"),
                trade_dict.get("quantity", 0),
                trade_dict.get("trade_time"),
                trade_dict.get("match_source"),
                trade_dict.get("trigger_trade_id"),
                trade_dict.get("fee"),
                trade_dict.get("net_amount"),
            ))
            conn.commit()

    def save_trades_batch(self, trades: List[dict]):
        """批量保存成交记录"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            for trade_dict in trades:
                cursor.execute("""
                    INSERT OR REPLACE INTO trades (
                        trade_id, order_id, symbol, side, price, quantity,
                        trade_time, match_source, trigger_trade_id, fee, net_amount
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade_dict.get("trade_id"),
                    trade_dict.get("order_id"),
                    trade_dict.get("symbol"),
                    trade_dict.get("side"),
                    trade_dict.get("price"),
                    trade_dict.get("quantity", 0),
                    trade_dict.get("trade_time"),
                    trade_dict.get("match_source"),
                    trade_dict.get("trigger_trade_id"),
                    trade_dict.get("fee"),
                    trade_dict.get("net_amount"),
                ))
            conn.commit()

    def get_trades(self, symbol: Optional[str] = None, start_time: Optional[str] = None,
                   end_time: Optional[str] = None, limit: int = 1000) -> List[dict]:
        """查询成交记录"""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM trades WHERE 1=1"
            params = []
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            if start_time:
                query += " AND trade_time >= ?"
                params.append(start_time)
            if end_time:
                query += " AND trade_time <= ?"
                params.append(end_time)
            query += " ORDER BY trade_time DESC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    # ─────────── 订单簿快照持久化 ───────────

    def save_snapshot(self, snapshot: dict, max_keep: int = 100):
        """保存订单簿快照，每个 symbol 最多保留 max_keep 条历史"""
        bids = snapshot.get("bids", [])
        asks = snapshot.get("asks", [])
        symbol = snapshot.get("symbol")
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO snapshots (
                    symbol, snapshot_time, best_bid, best_ask, spread,
                    bid_levels, ask_levels, total_bid_qty, total_ask_qty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                datetime.now().isoformat(),
                snapshot.get("best_bid"),
                snapshot.get("best_ask"),
                snapshot.get("spread"),
                json.dumps(bids),
                json.dumps(asks),
                sum(b["total_quantity"] for b in bids),
                sum(a["total_quantity"] for a in asks),
            ))
            # 清理过期快照，按 symbol 只保留最新的 max_keep 条
            if symbol:
                cursor.execute("""
                    DELETE FROM snapshots
                    WHERE id IN (
                        SELECT id FROM snapshots
                        WHERE symbol = ?
                        ORDER BY snapshot_time DESC
                        LIMIT -1 OFFSET ?
                    )
                """, (symbol, max_keep))
            conn.commit()

    def get_latest_snapshot(self, symbol: str) -> Optional[dict]:
        """获取最新订单簿快照"""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM snapshots WHERE symbol = ? ORDER BY snapshot_time DESC LIMIT 1",
                (symbol,)
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["bid_levels"] = json.loads(d["bid_levels"]) if d["bid_levels"] else []
                d["ask_levels"] = json.loads(d["ask_levels"]) if d["ask_levels"] else []
                return d
            return None

    # ─────────── 日终结算持久化 ───────────

    def save_settlement(self, symbol: str, account_dict: dict):
        """保存日终结算记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO settlements (
                    settle_date, symbol, cash, available_position, frozen_position,
                    today_bought_position, total_fees, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today, symbol,
                account_dict.get("cash"),
                account_dict.get("available_position", 0),
                account_dict.get("frozen_position", 0),
                account_dict.get("today_bought_position", 0),
                account_dict.get("total_fees"),
                account_dict.get("trade_count", 0),
            ))
            conn.commit()

    # ─────────── JSON 快照（快速恢复用）───────────

    def save_json_snapshot(self, symbol: str, data: dict):
        """保存 JSON 快照文件"""
        path = os.path.join(self.data_dir, f"snapshot_{symbol}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def load_json_snapshot(self, symbol: str) -> Optional[dict]:
        """加载 JSON 快照文件"""
        path = os.path.join(self.data_dir, f"snapshot_{symbol}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def export_to_csv(self, symbol: str, output_dir: Optional[str] = None) -> str:
        """导出指定标的的订单和成交记录到 CSV"""
        import csv
        out_dir = output_dir or self.data_dir
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.join(out_dir, f"{symbol}_{datetime.now().strftime('%Y%m%d')}")

        # 导出订单
        orders = self.get_orders(symbol=symbol, limit=100000)
        orders_path = f"{base}_orders.csv"
        if orders:
            with open(orders_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=orders[0].keys())
                writer.writeheader()
                writer.writerows(orders)

        # 导出成交
        trades = self.get_trades(symbol=symbol, limit=100000)
        trades_path = f"{base}_trades.csv"
        if trades:
            with open(trades_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=trades[0].keys())
                writer.writeheader()
                writer.writerows(trades)

        return f"Exported: {orders_path}, {trades_path}"
