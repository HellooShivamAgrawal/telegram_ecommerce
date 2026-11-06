
text_en = ({
    "start" : 
"""Tell me what you're looking for, or ask me to add a product to this chat's cart.
Use /cart to view and edit the cart, /show_categories to browse, and /help for more commands.""",
    "start_description" : "A wellcome message if you dont known this bot",
    "help" : 

"""Ask about products in ordinary chat, for example: "Add two notebooks to my cart."
/cart — view this chat's cart; /cart help — edit commands
/show_categories — browse categories
/search — search the catalogue
/cancel — leave catalogue browsing
/register — create an account before checkout
/language — choose your language

Group members share one cart. Cart items do not reserve stock; checkout uses the existing single-product flow.""",
    "help_description" : "A long message with all commands avaible for you, and how to use them.",
    "help_admin":
"""

You are admin. You can sell products and change theirs prices and add others admins.

The fallowing are instructions of how make this things:

To add a product you can type:
/add_product
To change the settings of a product you can search for it and for a button that does these actions.

To add others admin you can use the command(Note: have sure that the people already have an account in this e-commerce):
/add_admin username
""",
    "_language_name" : "English",
    "language_enter_code" : "Enter a supported language tag, such as en or pt.",
    "catalog_assistant_unavailable" : "The shopping assistant is unavailable right now. Use /search or /show_categories. If you requested a cart change, check /cart before trying it again.",
    "assistant_message_too_long": "Please keep shopping requests under 1,000 characters.",
    "assistant_cart_saved": "Your cart changes were saved, but I could not finish the reply. Use /cart to check your cart.",
    "assistant_cart_partial": "Some cart changes were saved, but I could not complete the whole request. Check /cart before trying again.",
    "assistant_cart_rejected": "I could not make that cart change. Your cart was not changed. Please specify the product and quantity, or use /cart help.",
    "activity_thinking": "Thinking…",
    "activity_searching": "Searching catalogue…",
    "activity_finding": "Finding {product}…",
    "activity_categories": "Browsing categories…",
    "activity_checking": "Checking product details…",
    "activity_checking_product": "Checking {product}…",
    "activity_cart": "Opening your cart…",
    "activity_adding": "Adding to your cart…",
    "activity_adding_product": "Adding {product} to your cart…",
    "activity_updating": "Updating your cart…",
    "activity_updating_product": "Updating {product} in your cart…",
    "activity_removing": "Removing from your cart…",
    "activity_removing_product": "Removing {product} from your cart…",
    "activity_clearing_cart": "Clearing your cart…",
    "cart_description": "View and edit this chat's cart",
    "cart_heading": "This chat's cart",
    "cart_empty": "This chat's cart is empty. Ask me to add a product, or use /cart add PRODUCT_ID QUANTITY.",
    "cart_total": "Total: {currency} {total}",
    "cart_stock_changed": "Only {quantity} currently available.",
    "cart_no_reservation": "Prices are current. Cart items do not reserve stock.",
    "cart_usage": "Cart commands:\n/cart — view cart\n/cart add PRODUCT_ID [QUANTITY]\n/cart set PRODUCT_ID QUANTITY (0 removes it)\n/cart remove PRODUCT_ID\n/cart clear\n\nEveryone in this chat shares the same cart.",
    "cart_invalid_quantity": "Use a quantity from 1 to 99. Use /cart set PRODUCT_ID 0 to remove an item.",
    "cart_insufficient_stock": "There is not enough stock for that quantity. Your cart was not changed.",
    "cart_product_not_found": "That product was not found. Use /search to find a product first.",
    "cart_full": "A cart can contain at most 50 different products. Remove one before adding another.",
    "cart_request_conflict": "That request has already been used for another change. Check /cart before sending a new command.",
    "cart_error": "I could not make that cart change. Check the command with /cart help.",
    "cart_unavailable": "The cart is unavailable right now. Check /cart before repeating a change.",
    "catalog_assistant_prompt" : "Tell me what kind of product you are looking for.",
    "choose_language" : "What is your language?",
    "selected_language" : "Selected language.",
    "language_dont_exist" : "These language is not supported.",
    "en" : "My language is 🇺🇸English",
    "pt" : "Minha linguagem é 🇵🇹Português",
    "register_description" : "Create a password to make purchase",
    "language_description" : "Change the language.", 
    "show_categories_description" : "See products by category",
    "cancel" : "❌Cancel",
    "previus" : "⬅️ Previus",
    "next" : "➡️ Next",
    "OK" : "👍OK",
    "price": "The price is: ",
    "rating": "The rating is: ",
    "purchased" : "Was purchased in units: ",
    "previus_product" : "⬅️ Previus product",
    "next_product" : "➡️ Next product",
    "product_details" : "View details",
    "buy" : "buy",
    "ask_if_want_create_a_password" : "Do you want to create a numeric password? (required to authenticate when making a purchase)",
    "ask_if_its_all_ok" : "Everything is alright?",
    "information_stored" : "Your information has been saved",
    "this_are_the_typed_password" : "This are the typed password. ",
    "canceled_operation" : "Operation canceled 🙁",
    "pin_too_short" : "Enter at least 6 digits before continuing.",
    "pin_too_long" : "The PIN can have at most 12 digits.",
    "pin_confirmation" : "Confirm your {length}-digit PIN?",
    "type_password" : "enter your password on the numeric keypad below:",
    "user_password_has_stored" : "Your password has stored, you can use her every time that you want to make a purchase.",
    "user_have_account" : "You already hava an account.",
    "user_dont_have_account" : "You dont have an account to make this operation. User the command /register to make an account.",
    "admin_only" : "Only an administrator can use this command.",
    "typing" : "You are typing: ",
    "this_is_not_a_number" : "The characters typed is not a number.",
    "this_is_not_a_integer" : "The characters typed is not a integer number.",
    "error_when_storing_photo" : "Photo dont stored, send the image as a photo instead as a file.",
    "this_is_not_a_valid_category": "This category does not exist. Use the /add_category command to create it.",
    "ask_for_category_name" : "Type the name of the category:",
    "ask_for_category_description" : "Type the description of the category:",
    "ask_for_category_tags" : "Type the tags of the category:",
    "ask_for_category_photo" : "Send me a photo that represent this category:",
    "ask_for_product_name" : "Type the name of the product:",
    "ask_for_product_description" : "Type the description of the product:",
    "ask_for_product_price" : "Type the price of the product:",
    "ask_for_quantity_in_stock" : "Type the number of units of this product available:",
    "ask_for_category_name_of_the_product" : "Type the name of the category which this product is in:",
    "ask_for_product_photo" : "Send me a photo that represent this product:",
    "information_dont_match" : "The database information dont match", 
    "error_in_orders" : "An error occurred when making this orders", 
    "successful_payment" : "Payment has been made! Thank you",
    "payment_needs_review" : "We received your payment, but this order needs review. Please contact the shop for fulfillment or a refund.",
    "ask_if_user_want_avaluate_the_product" : "You want to give your opinion on this product?",  
    "ask_for_the_rating" : "What is your opinion of this product?",
    "thanks_opinion" : "Thank you for your opinion! 😁 ",
    "rating" : "These are the rating of the people: ",
    "good" : "Good 😁 ",
    "bad" : "Bad 😞 ",
    "regular" : "Regular 🙂 ",
    "stock_empty" :"The stock are empty",
    "without_product_in_this_category" : "There is no product in this category",
    "ask_for_term_to_search" : "Type something to search for products:",
    "without_product_in_this_search" : "There are no products with these terms. Check for typos or search for something similar",
    "search_closest_matches": "Closest matches — choose a product:",
    "search_choice_stale": "This option is no longer available. Choose another product or use /search again.",
    "search_description" : "A command to search produts"
})



text_en["search_matching_choices"] = 'Which product would you like to see?'
