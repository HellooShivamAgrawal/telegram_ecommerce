from unicodedata import category as unicode_category

from sqlalchemy import case, func, or_, select, text

from telegram_ecommerce.database import models
from telegram_ecommerce.database.models import Session
from telegram_ecommerce.utils.utils import hash_password, verify_password
from telegram_ecommerce.utils.consts import admins


MAX_SEARCH_LENGTH = 100
MAX_SEARCH_TERMS = 8
MIN_FUZZY_TERM_LENGTH = 4
MIN_WORD_SIMILARITY = 0.5


def _search_words(phrase):
    # Keep combining marks attached to letters in scripts such as Devanagari.
    # Python's regex \w excludes marks and would split these words incorrectly.
    words = []
    word = ''
    for character in phrase:
        if character.isalnum() or (word and unicode_category(character).startswith('M')):
            word += character
        elif word:
            words.append(word)
            word = ''
    if word:
        words.append(word)
    return words


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

def search_products(string_to_search, limit=10):
    """Find available products, preferring lexical matches over name typos.

    Simple full-text search preserves multilingual words; English search also
    handles inflections such as notebook/notebooks. A typo fallback requires
    every query word to match, and never fuzzy-matches words shorter than four
    characters. Invalid or oversized queries fail closed without dropping terms.
    """
    if (not isinstance(string_to_search, str)
            or len(string_to_search) > MAX_SEARCH_LENGTH
            or '\x00' in string_to_search):
        return []
    phrase = ' '.join(string_to_search.split())
    words = _search_words(phrase.lower())
    if not words or len(words) > MAX_SEARCH_TERMS:
        return []
    words = list(dict.fromkeys(words))
    limit = max(1, min(int(limit), 50))

    product = models.Product
    document = product.name + text("' '") + product.description
    simple_document = func.to_tsvector(text("'simple'"), document)
    english_document = func.to_tsvector(text("'english'"), document)
    simple_name = func.to_tsvector(text("'simple'"), product.name)
    english_name = func.to_tsvector(text("'english'"), product.name)
    simple_query = func.plainto_tsquery(text("'simple'"), phrase)
    english_query = func.plainto_tsquery(text("'english'"), phrase)
    name_match = or_(
        simple_name.op('@@')(simple_query),
        english_name.op('@@')(english_query),
    )
    lexical_rank = func.greatest(
        func.ts_rank_cd(simple_document, simple_query),
        func.ts_rank_cd(english_document, english_query),
    )
    with Session() as session:
        stmt = (
            select(product)
            .where(
                or_(simple_document.op('@@')(simple_query),
                    english_document.op('@@')(english_query)),
                product.quantity_in_stock > 0,
            )
            .order_by(
                (func.lower(product.name) == func.lower(phrase)).desc(),
                name_match.desc(),
                lexical_rank.desc(),
                product.id,
            )
            .limit(limit)
        )
        lexical_results = session.scalars(stmt).all()
        if lexical_results or not any(len(word) >= MIN_FUZZY_TERM_LENGTH for word in words):
            return lexical_results

        # A small-shop fallback scans names only, with an explicit threshold.
        # It neither broadens successful lexical searches nor changes pooled
        # connections' pg_trgm settings. Every word remains an AND constraint.
        conditions = []
        scores = []
        for word in words:
            simple_word = func.plainto_tsquery(text("'simple'"), word)
            english_word = func.plainto_tsquery(text("'english'"), word)
            lexical_word = or_(
                simple_document.op('@@')(simple_word),
                english_document.op('@@')(english_word),
            )
            if len(word) < MIN_FUZZY_TERM_LENGTH:
                conditions.append(lexical_word)
                continue
            similarity = func.strict_word_similarity(word, func.lower(product.name))
            conditions.append(or_(lexical_word, similarity >= MIN_WORD_SIMILARITY))
            scores.append(case((lexical_word, 1.0), else_=similarity))

        stmt = (
            select(product)
            .where(product.quantity_in_stock > 0, *conditions)
            .order_by(sum(scores).desc(), product.id)
            .limit(limit)
        )
        return session.scalars(stmt).all()

