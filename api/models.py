"""
Dark-Store Inventory Allocation Engine: Pydantic Schemas
File: api/models.py
Description: Request and response validation models for FastAPI endpoints.
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class OrderItemRequest(BaseModel):
    sku_id: int = Field(..., description="Unique product SKU ID")
    qty_requested: int = Field(..., gt=0, description="Quantity of units requested")

class CreateOrderRequest(BaseModel):
    customer_id: int = Field(..., description="Customer ID placing the order")
    items: List[OrderItemRequest] = Field(..., min_length=1, description="List of items in basket")
    store_id: Optional[int] = Field(None, description="Explicit target dark store (optional)")
    allow_split_fulfillment: bool = Field(True, description="Enable multi-store 2PC split fulfillment if nearest store has partial stock")
    strategy: Optional[str] = Field("PESSIMISTIC", description="Allocation concurrency strategy: PESSIMISTIC, OCC, or ADVISORY")

class OrderItemDetail(BaseModel):
    order_item_id: int
    sku_id: int
    sku_name: Optional[str] = None
    qty_requested: int
    store_id_allocated: Optional[int] = None
    batch_id_allocated: Optional[int] = None
    unit_price: float
    status: str

class OrderResponse(BaseModel):
    order_id: int
    customer_id: int
    status: str
    idempotency_key: str
    total_amount: float
    created_at: datetime
    allocated_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    items: List[OrderItemDetail] = []
    is_idempotent_replay: bool = False

class StoreStockResponse(BaseModel):
    store_id: int
    store_name: str
    sku_id: int
    barcode: Optional[str] = None
    sku_name: str
    brand: Optional[str] = None
    category: str
    batch_id: int
    qty_available: int
    qty_reserved: int
    expiry_date: str

class AnalyticsOverview(BaseModel):
    total_stores: int
    total_skus: int
    total_orders_today: int
    allocated_orders: int
    split_orders: int
    failed_orders: int
    active_riders: int
    understocked_alerts: int
