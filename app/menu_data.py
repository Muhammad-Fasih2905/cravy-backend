"""The real Cravy menu (prices in PKR). Source: the product data supplied by the owner.
Final price of a size = product price + that size's additional price (e.g. Mango DOUBLE = 160 + 115 = 275).
Images are NOT set here: the admin uploads a WebP image for each product from the admin panel."""

CATEGORIES = [  # (slug, name)
    ("ice-cream", "Ice Cream"),
    ("margaritas", "Margaritas"),
    ("mocktails", "Mocktails"),
    ("fresh-juices", "Fresh Juices"),
    ("coladas", "Coladas"),
    ("soda", "Soda"),
    ("ice-cream-shakes", "Ice Cream Shakes"),
    ("milk-shakes", "Milk Shakes"),
]

SCOOPS = [("SINGLE", 0), ("DOUBLE", 115)]
MARGARITA_SIZES = [("REGULAR", 0), ("LARGE", 140)]
DRINK_SIZES = [("REGULAR", 0), ("LARGE", 145)]

# (slug, name, category slug, description, price, sizes as (label, additional_price) or [], featured)
PRODUCTS = [
    ("mango", "Mango", "ice-cream", "Tropical mango ice cream, bursting with the sweet and tangy essence of ripe mangoes for a vibrant, fruity treat.", 160, SCOOPS, False),
    ("blue-berry", "Blue Berry", "ice-cream", "Creamy ice cream blended with the tart and sweet flavor of fresh blueberries, creating a refreshing dessert experience.", 160, SCOOPS, False),
    ("white-vanilla-premium", "White Vanilla Premium", "ice-cream", "A smooth, premium vanilla ice cream with a delicate flavor, crafted from high-quality ingredients for a classic taste.", 160, SCOOPS, False),
    ("caramel-crunch", "Caramel Crunch", "ice-cream", "A rich ice cream infused with sweet caramel and delightful crunchy pieces, offering a satisfying texture in every spoonful.", 160, SCOOPS, False),
    ("cheese-cake", "Cheese Cake", "ice-cream", "Smooth cheesecake flavored ice cream swirled with rich graham cracker crust pieces, offering a dessert-inspired delight.", 160, SCOOPS, False),
    ("chocolate-chips", "Chocolate Chips", "ice-cream", "Creamy ice cream generously studded with rich, delectable chocolate chips, providing a delightful crunch and cocoa flavor.", 160, SCOOPS, False),
    ("pista-nut", "Pista Nut", "ice-cream", "Smooth, nutty pistachio ice cream, enriched with finely chopped pistachios for a subtle flavor and satisfying texture.", 160, SCOOPS, False),
    ("king-kulfa", "King Kulfa", "ice-cream", "A dense and creamy traditional south asian ice cream, spiced with cardamom and pistachio, for an aromatic and rich experience.", 160, SCOOPS, False),
    ("tutti-frutti", "Tutti Frutti", "ice-cream", "A lively ice cream featuring a medley of candied fruits, offering a colorful and sweet taste in every scoop.", 160, SCOOPS, False),
    ("chocolate-fudge-brownie", "Chocolate Fudge Brownie", "ice-cream", "Rich chocolate ice cream swirled with decadent fudge and chunks of chewy brownie, a deeply satisfying dessert.", 160, SCOOPS, False),
    ("strawberry-ripple", "Strawberry Ripple", "ice-cream", "Creamy ice cream swirled with ribbons of sweet and tangy strawberry puree, offering a refreshing fruity twist.", 160, SCOOPS, False),
    ("oreo", "Oreo", "ice-cream", "Creamy ice cream mixed with crushed oreo cookie pieces, creating a delightful blend of chocolate and cream in each bite.", 160, SCOOPS, False),
    ("pine-apple", "Pine Apple", "ice-cream", "Refreshing pineapple ice cream, featuring the sweet and tangy essence of ripe pineapple for a bright, fruity treat.", 160, SCOOPS, False),

    ("apple-mint-margarita", "Apple Mint Margarita", "margaritas", "A crisp and refreshing non-alcoholic margarita, blending the sweet notes of apple with a cool hint of fresh mint.", 360, MARGARITA_SIZES, True),
    ("lemon-margarita", "Lemon Margarita", "margaritas", "A zesty and refreshing non-alcoholic lemon margarita, blending tart lemon juice with a touch of sweetness.", 360, MARGARITA_SIZES, True),
    ("mint-margarita", "Mint Margarita", "margaritas", "A cool and invigorating non-alcoholic mint margarita, combining fresh mint with a refreshing tangy base.", 360, MARGARITA_SIZES, True),

    ("blue-lagoon", "Blue Lagoon", "mocktails", "A refreshing mocktail with a vibrant blue hue, offering a sweet and citrusy blend of flavors.", 500, DRINK_SIZES, False),
    ("sunrise", "Sunrise", "mocktails", "A delightful mocktail with layers of fruity flavors, reminiscent of a colorful sunrise.", 500, DRINK_SIZES, False),
    ("strawberry-mojito", "Strawberry Mojito", "mocktails", "A refreshing mocktail blending fresh strawberries with mint and lime, topped with sparkling soda.", 500, DRINK_SIZES, False),
    ("golden-beach", "Golden Beach", "mocktails", "A sunny mocktail with tropical fruit flavors, creating a sweet and tangy refreshment.", 500, DRINK_SIZES, False),
    ("fruit-punch", "Fruit Punch", "mocktails", "A sweet and tangy blend of various fruit juices, offering a burst of tropical flavors.", 500, DRINK_SIZES, False),

    ("carrot-juice", "Carrot Juice", "fresh-juices", "Freshly squeezed carrot juice, offering a naturally sweet and earthy taste.", 430, DRINK_SIZES, False),
    ("abc-juice", "Abc Juice", "fresh-juices", "Apple beetroot carrot", 500, DRINK_SIZES, False),
    ("apple-juice", "Apple Juice", "fresh-juices", "Pure, freshly squeezed apple juice, providing a crisp and naturally sweet flavor.", 430, DRINK_SIZES, False),

    ("pina-colada", "Pina Colada", "coladas", "A tropical blend of creamy coconut and sweet pineapple, served chilled as a refreshing non-alcoholic colada.", 400, [], False),
    ("blue-colada", "Blue Colada", "coladas", "A vibrant, refreshing blue colada, featuring a sweet and exotic fruit blend for a cool, tropical escape.", 400, [], False),

    ("blue-berry-soda", "Blue Berry", "soda", "A crisp and bubbly soda infused with the sweet and slightly tart essence of blueberries.", 215, [], False),
    ("lemon-soda", "Lemon", "soda", "A sparkling soda with a bright and zesty lemon flavor, offering a refreshing citrus taste.", 215, [], False),
    ("peach-soda", "Peach", "soda", "A refreshing soda with the sweet and fruity essence of ripe peaches, offering a light and effervescent beverage experience.", 215, [], False),
    ("apple-soda", "Apple", "soda", "A crisp and invigorating soda infused with the natural sweetness and tart notes of fresh apples, creating a delightful bubbly drink.", 215, [], False),
    ("boster-power", "Boster Power", "soda", "An energizing soda designed to invigorate, offering a unique and refreshing flavor profile for a revitalizing boost.", 215, [], False),

    ("banana-shake", "Banana Shake", "ice-cream-shakes", "A creamy ice cream shake made with ripe bananas, blended to a smooth and delightful consistency.", 500, [], False),
    ("cookie-shake", "Cookie Shake", "ice-cream-shakes", "A rich ice cream shake blended with crumbled cookies, offering a delightful textual contrast.", 500, [], False),
    ("chocolate-shake", "Chocolate Shake", "ice-cream-shakes", "A decadent ice cream shake crafted with rich chocolate, blended into a velvety smooth beverage.", 500, [], False),
    ("strawberry-shake", "Strawberry Shake", "ice-cream-shakes", "A creamy ice cream shake featuring sweet, fresh strawberries, blended for a smooth texture.", 500, [], False),
    ("cold-coffee", "Cold Coffee", "ice-cream-shakes", "A chilled coffee blended with ice cream for a smooth, creamy, and invigorating treat.", 500, [], False),
    ("vanilla-shake", "Vanilla Shake", "ice-cream-shakes", "A classic ice cream shake with smooth vanilla flavor, blended to a rich and creamy consistency.", 500, [], False),

    ("banana-dates-shake", "Banana Dates Shake", "milk-shakes", "A rich milk shake combining sweet dates and ripe bananas for a naturally flavorful drink.", 430, [], False),
    ("banana-milk-shake", "Banana Shake", "milk-shakes", "A creamy milk shake made with ripe bananas, blended for a smooth and satisfying treat.", 360, [], False),
]
