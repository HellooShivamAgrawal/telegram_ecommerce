from sqlalchemy import select

from telegram_ecommerce.database import models
from telegram_ecommerce.database.models import Session
from telegram_ecommerce.database.search import search_catalog, search_products
from telegram_ecommerce.utils.utils import hash_password, verify_password
from telegram_ecommerce.utils.consts import admins


def user_exist(user_id):
    with Session() as session:
        return bool(session.get(models.Customer, user_id))

def is_admin(user_id):
    return user_id in admins and user_exist(user_id)

def get_password(user_id):
    with Session() as session:
        user = session.get(models.Customer, user_id)
        return user.password_hash

def check_password(user_id, password):
    with Session() as session:
        user = session.get(models.Customer, user_id)
        if user is None:
            return False
        valid, legacy = verify_password(password, user.password_hash)
        if valid and legacy:
            user.password_hash = hash_password(password)
            session.commit()
        return valid

def get_name_of_all_categories():
    with Session() as session:
        return session.scalars(select(models.Category.name)).all()

def get_category_id_from_name(name):
    with Session() as session:
        stmt = select(models.Category.id).where(models.Category.name == name)
        return session.scalars(stmt).first()


def get_all_available_by_category_id(category_id):
    with Session() as session:
        stmt = (
            select(models.Product)
            .where(models.Product.category_id == category_id)
            .where(models.Product.quantity_in_stock > 0)
        )
        return session.scalars(stmt).all()

def get_all_available_by_category_name(name):
    category_id = get_category_id_from_name(name)
    return get_all_available_by_category_id(category_id)

def get_available_product(product_id):
    with Session() as session:
        stmt = select(models.Product).where(
            models.Product.id == product_id,
            models.Product.quantity_in_stock > 0,
        )
        return session.scalar(stmt)

def get_ratings_of_a_product(product_id):
    with Session() as session:
        stmt = (
            select(models.Order.rating)
            .where(models.Order.product_id == product_id)
            .where(models.Order.rating != None)
        )
        return session.scalars(stmt).all()

def count_occurrence_of_specified_rating(product_id, rating):
    all_ratings = get_ratings_of_a_product(product_id)
    return all_ratings.count(rating)

