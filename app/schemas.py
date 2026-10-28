from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class VariantOut(Out):
    id: int
    name: str
    price: int
    is_available: bool


class AddonOut(Out):
    id: int
    name: str
    price: int
    is_available: bool


class ProductOut(Out):
    id: int
    category_id: int
    name: str
    slug: str
    description: str | None
    image_url: str | None
    base_price: int
    is_available: bool
    is_bestseller: bool
    is_featured: bool
    prep_minutes: int | None
    is_demo: bool
    variants: list[VariantOut]
    addons: list[AddonOut]


class CategoryOut(Out):
    id: int
    name: str
    slug: str
    products: list[ProductOut]


class MenuOut(BaseModel):
    categories: list[CategoryOut]


class PublicSettings(Out):
    business_name: str
    phone: str | None
    announcement: str | None
    delivery_charge: int
    min_order_value: int
    tax_percent: int
    cod_enabled: bool
    easypaisa_enabled: bool
    easypaisa_name: str | None
    easypaisa_number: str | None


class CartLineIn(BaseModel):
    product_id: int
    variant_id: int | None = None
    addon_ids: list[int] = Field(default_factory=list, max_length=20)
    quantity: int = Field(ge=1, le=50)


class QuoteIn(BaseModel):
    items: list[CartLineIn] = Field(min_length=1, max_length=50)
    coupon_code: str | None = Field(default=None, max_length=40)
    zone_id: int | None = None


class QuoteLine(BaseModel):
    product_id: int
    product_name: str
    variant_name: str | None
    addons: list[dict]
    unit_price: int
    quantity: int
    line_total: int


class QuoteOut(BaseModel):
    lines: list[QuoteLine]
    subtotal: int
    delivery_charge: int
    discount_total: int
    tax_total: int = 0
    total: int
    coupon_code: str | None
    meets_minimum: bool
    min_order_value: int
    delivery_pending: bool = False  # true when a delivery area must still be chosen
    zone_name: str | None = None


class OrderIn(QuoteIn):
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str = Field(pattern=r"^[0-9+\-\s]{10,20}$")
    customer_email: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=2, max_length=80)
    address: str = Field(min_length=5, max_length=300)
    landmark: str | None = Field(default=None, max_length=160)
    instructions: str | None = Field(default=None, max_length=300)
    payment_method: Literal["COD", "EASYPAISA"]


class PaymentInstructions(BaseModel):
    method: str
    account_number: str
    account_name: str | None
    amount: int
    reference: str  # the order number: customer should mention it in the Easypaisa payment note
    message: str


class OrderOut(Out):
    order_number: str
    status: str
    subtotal: int
    delivery_charge: int
    discount_total: int
    tax_total: int = 0
    total: int
    payment_method: str
    payment_status: str
    payment_instructions: PaymentInstructions | None = None  # only while an Easypaisa payment is still due


class ZoneOut(Out):
    id: int
    name: str
    city: str
    charge: int
    free_above: int | None
    min_order: int | None
