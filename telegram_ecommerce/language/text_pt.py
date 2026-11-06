
text_pt = ({
    "start" : 
"""Diga o que procura ou peça para adicionar um produto ao carrinho desta conversa.
Use /cart para ver e editar o carrinho, /show_categories para navegar e /help para mais comandos.""",
    "start_description" : "Uma mensagem de boas vindas.",
    "help" : 

"""Pergunte sobre produtos na conversa, por exemplo: "Adicione dois cadernos ao carrinho."
/cart — ver o carrinho desta conversa; /cart help — comandos de edição
/show_categories — navegar pelas categorias
/search — pesquisar no catálogo
/cancel — sair da navegação do catálogo
/register — criar uma conta antes de comprar
/language — escolher o idioma

Os membros do grupo compartilham um carrinho. Os itens não reservam estoque; a compra continua pelo fluxo existente de um produto.""",
    "help_description" : "A long message with all commands avaible for you, and how to use them.",
    "help_admin":
"""

Você é administrador, você pode vender produtos, mudar seus preços e adicionar outros administradores.

Aqui estão instruções de como fazer isso:

Para adicionar produtos você pode digitar:
/add_product
Para mudar as informações sobre o produto você pode pesquisar por ele e por um botão que faça essas ações.

Para adicionar outros administradores você pode usar o comando:
/add_admin username
""",
    "_language_name" : "Português",
    "language_enter_code" : "Digite uma tag de idioma disponível, como en ou pt.",
    "catalog_assistant_unavailable" : "O assistente de compras está indisponível no momento. Use /search ou /show_categories. Se pediu uma alteração no carrinho, confira /cart antes de tentar novamente.",
    "assistant_message_too_long": "Limite seu pedido de compras a 1.000 caracteres.",
    "assistant_cart_saved": "As alterações no carrinho foram salvas, mas não consegui concluir a resposta. Use /cart para conferir seu carrinho.",
    "assistant_cart_partial": "Algumas alterações foram salvas, mas não consegui concluir o pedido inteiro. Confira /cart antes de tentar novamente.",
    "assistant_cart_rejected": "Não consegui fazer essa alteração. Seu carrinho não foi alterado. Informe o produto e a quantidade ou use /cart help.",
    "activity_thinking": "Pensando…",
    "activity_searching": "Pesquisando no catálogo…",
    "activity_finding": "Procurando {product}…",
    "activity_categories": "Consultando categorias…",
    "activity_checking": "Consultando detalhes do produto…",
    "activity_checking_product": "Consultando {product}…",
    "activity_cart": "Abrindo seu carrinho…",
    "activity_cart_saved": "Alteração no carrinho salva. Preparando sua resposta…",
    "activity_adding": "Adicionando ao seu carrinho…",
    "activity_adding_product": "Adicionando {product} ao seu carrinho…",
    "activity_updating": "Atualizando seu carrinho…",
    "activity_updating_product": "Atualizando {product} no seu carrinho…",
    "activity_removing": "Removendo do seu carrinho…",
    "activity_removing_product": "Removendo {product} do seu carrinho…",
    "activity_clearing_cart": "Esvaziando seu carrinho…",
    "cart_description": "Veja e edite o carrinho desta conversa",
    "cart_heading": "Carrinho desta conversa",
    "cart_empty": "O carrinho desta conversa está vazio. Peça para adicionar um produto ou use /cart add ID_DO_PRODUTO QUANTIDADE.",
    "cart_total": "Total: {currency} {total}",
    "cart_stock_changed": "Apenas {quantity} disponíveis no momento.",
    "cart_no_reservation": "Os preços são os atuais. Itens no carrinho não reservam estoque.",
    "cart_usage": "Comandos do carrinho:\n/cart — ver carrinho\n/cart add ID_DO_PRODUTO [QUANTIDADE]\n/cart set ID_DO_PRODUTO QUANTIDADE (0 remove o item)\n/cart remove ID_DO_PRODUTO\n/cart clear\n\nTodos nesta conversa compartilham o mesmo carrinho.",
    "cart_invalid_quantity": "Use uma quantidade de 1 a 99. Use /cart set ID_DO_PRODUTO 0 para remover um item.",
    "cart_insufficient_stock": "Não há estoque suficiente para essa quantidade. Seu carrinho não foi alterado.",
    "cart_product_not_found": "Esse produto não foi encontrado. Use /search para procurar um produto primeiro.",
    "cart_full": "Um carrinho pode conter até 50 produtos diferentes. Remova um antes de adicionar outro.",
    "cart_request_conflict": "Esse pedido já foi usado para outra alteração. Confira /cart antes de enviar um novo comando.",
    "cart_error": "Não consegui alterar o carrinho. Confira os comandos com /cart help.",
    "cart_unavailable": "O carrinho está indisponível no momento. Confira /cart antes de repetir uma alteração.",
    "catalog_assistant_prompt" : "Diga que tipo de produto você está procurando.",
    "choose_language" : "Qual é sua linguagem?",
    "selected_language" : "Linguagem selecionada.",
    "language_dont_exist" : "Não possui suporte a essa linguagem.",
    "en" : "My language is 🇺🇸English",
    "pt" : "Minha linguagem é 🇵🇹Português",
    "register_description" : "Criar senha para fazer compras",
    "language_description" : "Mudar a linguagem.", 
    "show_categories_description" : "Ver produtos por categoria",
    "cancel" : "❌Cancelar",
    "previus" : "⬅️ Anterior",
    "next" : "➡️ Próximo",
    "OK" : "👍Certo",
    "price": "O preço é: ",
    "rating": "A avaliação é: ",
    "purchased" : "Foi comprado em unidades: ",
    "previus_product" : "⬅️ Produto anterior",
    "next_product" : "➡️ Próximo produto",
    "product_details" : "Ver detalhes",
    "buy" : "Comprar",
    "ask_if_want_create_a_password" : "Do you want to create a numeric password? (required to authenticate when making a purchase)",
    "ask_if_want_create_a_password" : "Do you want to create a numeric password? (required to authenticate when making a purchase)",
    "ask_if_want_create_a_password" : "Você quer criar uma senha numérica? (Necessário para fazer compras)",
    "ask_if_its_all_ok" : "Está tudo correto?",
    "information_stored" : "Suas informações foram salvas",
    "this_are_the_typed_password" : "Essa foi a senha digitada. ",
    "canceled_operation" : "Operação cancelada 🙁",
    "pin_too_short" : "Digite pelo menos 6 dígitos antes de continuar.",
    "pin_too_long" : "O PIN pode ter no máximo 12 dígitos.",
    "pin_confirmation" : "Confirma seu PIN de {length} dígitos?",
    "type_password" : "Digite a senha no teclado numérico abaixo:",
    "user_password_has_stored" : "Your password has stored, you can use her every time that you want to make a purchase.",
    "user_have_account" : "Você já possui uma conta",
    "user_dont_have_account" : "You dont have an account to make this operation. User the command /register to make an account.",
    "admin_only" : "Apenas um administrador pode usar este comando.",
    "typing" : "Você está digitando: ",
    "this_is_not_a_number" : "Os carácteres digitadas não são números",
    "this_is_not_a_integer" : "Os carácteres digitadas não são números inteiros",
    "error_when_storing_photo" : "Foto não armazenada, envie a foto como imagem ao invés de como arquivo.",
    "this_is_not_a_valid_category": "Essa categoria não existe. Use o comando /add_category para criá-la",
    "ask_for_category_name" : "Digite o nome da categoria:",
    "ask_for_category_description" : "Digite a descrição da categoria:",
    "ask_for_category_tags" : "Digite as tags dessa categoria:",
    "ask_for_category_photo" : "Me envie uma foto que represente essa categoria:",
    "ask_for_product_name" : "Digite o nome do produto:",
    "ask_for_product_description" : "Digite a desrição do produto:",
    "ask_for_product_price" : "Digite o preço do produto:",
    "ask_for_quantity_in_stock" : "Digite a quantidade desse produto no estoque:",
    "ask_for_category_name_of_the_product" : "Digite o nome da categoria que o produto está:",
    "ask_for_product_photo" : "Me envie uma foto que represente esse produto:",
    "information_dont_match" : "As informações do banco de dados não batem",
    "error_in_orders" : "Um erro ocorreu quando suas informações seriam armazenadas",
    "successful_payment" : "O pagamento foi recebido. Muito Obrigado!",
    "payment_needs_review" : "Recebemos o pagamento, mas este pedido precisa de revisão. Entre em contato com a loja para entrega ou reembolso.",
    "ask_if_user_want_avaluate_the_product" : "Você quer dar sua opinião sobre esse produto?",
    "ask_for_the_rating" : "O que você acha desse produto?",
    "thanks_opinion" : "Obrigado por sua opinião! 😁 ",
    "rating" : "Essas são as avaliações das pessoas: ", 
    "good" : "Bom 😁 ",
    "bad" : "Ruim 😞 ",
    "regular" : "Normal 🙂 ",
    "stock_empty" : "O estoque está vazio",
    "without_product_in_this_category" : "Não há produtos nessa categoria",
    "ask_for_term_to_search" : "Digite termos para pesquisar por produtos:",
    "without_product_in_this_search" : "Não há produtos com esses termos. Verifique erros de escrita ou tente algo similar.",
    "search_description" : "Comando para pesquisar produtos"
})


