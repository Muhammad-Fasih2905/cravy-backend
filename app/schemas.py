from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .discounts import apply_discount


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class VariantOut(Out):
    id: int
    name: str
    price: int  # regular price
    is_available: bool
    final_price: int = 0  # price after the product discount: what the customer pays


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
    discount_percent: int = 0
    final_base_price: int = 0  # base_price after the discount
    variants: list[VariantOut]
    addons: list[AddonOut]

    @model_validator(mode="after")
    def _apply_discount(self):
        self.final_base_price = apply_discount(self.base_price, self.discount_percent)
        for v in self.variants:
            v.final_price = apply_discount(v.price, self.discount_percent)
        return self


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
    # Customer's map pin. The server measures the distance to the shop and applies the fixed delivery rates.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class QuoteLine(BaseModel):
    product_id: int
    product_name: str
    variant_name: str | None
    addons: list[dict]
    unit_price: int
    quantity: int
    line_total: int
    discount_percent: int = 0  # the product discount already included in unit_price


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
    delivery_pending: bool = False  # true until the customer's location is known
    distance_km: float | None = None
    delivery_band: str | None = None  # e.g. "2-3 km"
    deliverable: bool = True  # false when the location is beyond the delivery range
    delivery_message: str | None = None


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
