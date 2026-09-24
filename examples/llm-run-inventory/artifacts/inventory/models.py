"""Data model definitions for the inventory service."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Product:
    id: int
    sku: str
    name: str
    description: str
    created_at: str
    updated_at: str


@dataclass
class Warehouse:
    id: int
    code: str
    name: str
    location: str
    created_at: str
    updated_at: str


@dataclass
class StockItem:
    id: int
    product_id: int
    warehouse_id: int
    quantity: int
    reserved_quantity: int
    version: int
    updated_at: str


@dataclass
class StockAdjustment:
    id: int
    stock_item_id: int
    delta: int
    resulting_quantity: int
    reason: str
    actor: str
    correlation_id: Optional[str]
    created_at: str


@dataclass
class Threshold:
    id: int
    product_id: int
    warehouse_id: Optional[int]
    min_quantity: int
    created_at: str
    updated_at: str


@dataclass
class Alert:
    id: int
    product_id: int
    warehouse_id: int
    current_quantity: int
    threshold_value: int
    status: str
    created_at: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
