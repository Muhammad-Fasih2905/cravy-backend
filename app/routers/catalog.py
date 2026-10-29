from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import models
from ..db import get_db
from ..pricing import get_settings
from ..delivery import MAX_KM, rates_table
from ..schemas import MenuOut, ProductOut, PublicSettings

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/menu", response_model=MenuOut)
def menu(response: Response, db: Session = Depends(get_db)):
    """One call returns the whole menu so the frontend can preload it."""
    response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=300"
    cats = db.scalars(
        select(models.Category)
        .where(models.Category.is_active)
        .order_by(models.Category.sort_order)
        .options(
            selectinload(models.Category.products).selectinload(models.Product.variants),
            selectinload(models.Category.products).selectinload(models.Product.addons),
        )
    ).all()
    return {"categories": cats}


@router.get("/products/{slug}", response_model=ProductOut)
def product(slug: str, db: Session = Depends(get_db)):
    p = db.scalar(
        select(models.Product).where(models.Product.slug == slug)
        .options(selectinload(models.Product.variants), selectinload(models.Product.addons))
    )
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


@router.get("/settings", response_model=PublicSettings)
def public_settings(db: Session = Depends(get_db)):
    s = get_settings(db)
    db.commit()
    return s


@router.get("/delivery-rates")
def delivery_rates():
    """The fixed delivery price list (by distance from the shop). Same rates for every order."""
    return {"max_km": MAX_KM, "rates": rates_table()}
