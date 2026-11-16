"""Django-style admin site at /admin (SQLAdmin). Only the super admin (the ADMIN_EMAIL account) can log in.
It does not touch the API: the website's own admin panel keeps working through /api/admin/*."""
import logging
import time

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from . import models
from .config import settings
from .db import SessionLocal, engine
from .security import _admin_from_token, is_super_admin, make_token, verify_password

log = logging.getLogger("superadmin")
_tries: dict[str, list[float]] = {}


class SuperAdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        ip = request.client.host if request.client else "?"
        recent = [t for t in _tries.get(ip, []) if t > time.time() - 60]
        _tries[ip] = recent + [time.time()]
        if len(recent) >= 5:  # same limit as the API login: 5 attempts a minute
            return False
        form = await request.form()
        email, password = str(form.get("username", "")).strip().lower(), str(form.get("password", ""))
        with SessionLocal() as db:
            a = db.scalar(select(models.AdminUser).where(models.AdminUser.email == email))
            if a is None or not a.is_active or not verify_password(password, a.password_hash) or not is_super_admin(a):
                return False
            request.session.update({"token": make_token(a.id)})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token")
        if not token:
            return False
        try:
            with SessionLocal() as db:
                _admin_from_token(token, db)  # valid, not expired, and still the super admin
            return True
        except Exception:  # noqa: BLE001
            return False


class CategoryAdmin(ModelView, model=models.Category):
    name, name_plural, icon = "Category", "Categories", "fa-solid fa-list"
    column_list = [models.Category.id, models.Category.name, models.Category.slug, models.Category.sort_order, models.Category.is_active]
    column_searchable_list = [models.Category.name, models.Category.slug]
    form_columns = ["name", "slug", "sort_order", "is_active"]
    can_delete = False  # deleting a category would take its products with it


class ProductAdmin(ModelView, model=models.Product):
    name, name_plural, icon = "Product", "Products", "fa-solid fa-ice-cream"
    column_list = [models.Product.id, models.Product.name, models.Product.category, models.Product.base_price, models.Product.discount_percent,
                   models.Product.is_available, models.Product.is_featured, models.Product.is_bestseller]
    column_searchable_list = [models.Product.name, models.Product.slug]
    column_sortable_list = [models.Product.id, models.Product.name, models.Product.base_price, models.Product.is_available]
    form_columns = ["name", "slug", "category", "description", "base_price", "discount_percent", "is_available", "is_featured", "is_bestseller", "sort_order"]
    can_create = False  # new products (with sizes, toppings and photos) are added from the website's admin panel
    can_delete = False
    page_size = 50


class OrderAdmin(ModelView, model=models.Order):
    name, name_plural, icon = "Order", "Orders", "fa-solid fa-bag-shopping"
    column_list = [models.Order.order_number, models.Order.status, models.Order.customer_name, models.Order.customer_phone, models.Order.total, models.Order.created_at]
    column_searchable_list = [models.Order.order_number, models.Order.customer_phone, models.Order.customer_name]
    column_default_sort = [("created_at", True)]
    can_create = can_edit = can_delete = False  # orders are only viewed here
    page_size = 50


class PaymentAdmin(ModelView, model=models.Payment):
    name, name_plural, icon = "Payment", "Payments", "fa-solid fa-money-bill"
    column_list = [models.Payment.id, models.Payment.order_id, models.Payment.method, models.Payment.status, models.Payment.amount]
    can_create = can_edit = can_delete = False


class CouponAdmin(ModelView, model=models.Coupon):
    name, name_plural, icon = "Coupon", "Coupons", "fa-solid fa-tag"
    column_list = [models.Coupon.id, models.Coupon.code, models.Coupon.type, models.Coupon.value, models.Coupon.used_count, models.Coupon.is_active]
    column_searchable_list = [models.Coupon.code]


class AdminUserAdmin(ModelView, model=models.AdminUser):
    name, name_plural, icon = "Admin user", "Admin users", "fa-solid fa-user-shield"
    column_list = [models.AdminUser.id, models.AdminUser.email, models.AdminUser.is_active]
    can_create = can_edit = can_delete = False  # passwords are never shown or changed here: use python -m app.reset_admin


def setup_superadmin(app) -> None:
    admin = Admin(app, engine, authentication_backend=SuperAdminAuth(secret_key=settings.secret_key), base_url="/admin", title="Cravy Super Admin")
    for v in (CategoryAdmin, ProductAdmin, OrderAdmin, PaymentAdmin, CouponAdmin, AdminUserAdmin):
        admin.add_view(v)
    log.info("Super admin site ready at /admin")