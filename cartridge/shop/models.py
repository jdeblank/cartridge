
from copy import copy
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models.base import ModelBase
from django.template.defaultfilters import slugify, striptags
from django.utils.translation import ugettext, ugettext_lazy as _

from cartridge.shop.fields import OptionField, MoneyField, SKUField, DiscountCodeField
from cartridge.shop import managers
from cartridge.shop.settings import ORDER_STATUS_CHOICES, OPTION_TYPE_CHOICES


class Displayable(models.Model):
    """
    Abstract model representing a visible object on the website with common 
    functionality such as auto slug creation and an active field for toggling 
    visibility. Inherited by Category and Product models.
    """

    title = models.CharField(_("Title"), max_length=100)
    slug = models.SlugField(max_length=100, editable=False, null=True)
    active = models.BooleanField(_("Visible on the site"), default=False)
        
    objects = managers.ActiveManager()

    class Meta:
        abstract = True

    def __unicode__(self):
        return self.title
        
    def save(self, *args, **kwargs):
        """
        Create a unique slug from the title by appending an index.
        """
        if self.id is None:
            self.slug = self.get_slug()
            i = 0
            while True:
                if i > 0:
                    self.slug = "%s-%s" % (self.slug, i)
                if not self.__class__.objects.filter(slug=self.slug):
                    break
                i += 1
        super(Displayable, self).save(*args, **kwargs)
        
    def get_absolute_url(self):
        url_name = self.__class__.__name__.lower()
        return reverse("shop_%s" % url_name, kwargs={"slug": self.slug})
        
    def get_slug(self):
        return slugify(self.title)
    
    def admin_link(self):
        if not self.active:
            return ""
        return "<a href='%s'>%s</a>" % (self.get_absolute_url(), 
            ugettext("View on site"))
    admin_link.allow_tags = True
    admin_link.short_description = ""

class ProductOption(models.Model):
    """
    A selectable option for a product such as size or colour.
    """
    
    type = models.IntegerField(_("Type"), choices=OPTION_TYPE_CHOICES)
    name = OptionField(_("Name"))
    
    objects = managers.ProductOptionManager()

    def __unicode__(self):
        return "%s: %s" % (self.get_type_display(), self.name)

    class Meta:
        verbose_name = _("Product Option")
        verbose_name_plural = _("Product Options")

class Category(Displayable):
    """
    A category of products on the website.
    """

    parent = models.ForeignKey("self", blank=True, null=True, 
        related_name="children")
    titles = models.CharField(editable=False, max_length=1000, blank=True, 
        null=True)
    ordering = models.IntegerField(editable=False, null=True)

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        ordering = ("titles",)

    def __unicode__(self):
        return self.titles
        
    def save(self, *args, **kwargs):
        """
        Create the titles field using the titles up the parent chain and set 
        the initial value for ordering.
        """
        if self.id is None:
            titles = [self.title]
            parent = self.parent
            while parent is not None:
                titles.insert(0, parent.title)
                parent = parent.parent
            self.titles = " / ".join(titles)
            self.ordering = Category.objects.filter(parent=self.parent).count()
        super(Category, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Update the ordering values for the sibling categories.
        """
        Category.objects.filter(parent=self.parent, ordering__gte=self.ordering
            ).update(ordering=models.F("ordering") - 1)
        super(Category, self).delete(*args, **kwargs)
        
    def get_slug(self):
        slug = slugify(self.title)
        if self.parent is not None:
            return "%s/%s" % (self.parent.get_slug(), slug)
        return slug

class Priced(models.Model):
    """
    Abstract model with unit and sale price fields. Inherited by Product and 
    ProductVariation models.
    """

    unit_price = MoneyField(_("Unit price"))
    sale_id = models.IntegerField(null=True)
    sale_price = MoneyField(_("Sale price"))
    sale_from = models.DateTimeField(_("Sale start"), blank=True, null=True)
    sale_to = models.DateTimeField(_("Sale end"), blank=True, null=True)

    class Meta:
        abstract = True

    def on_sale(self):
        """
        Returns True if the sale price is applicable.
        """
        valid_from = self.sale_from is None or self.sale_from < datetime.now()
        valid_to = self.sale_to is None or self.sale_to > datetime.now()
        return self.sale_price is not None and valid_from and valid_to

    def has_price(self):
        """
        Returns True if there is a valid price.
        """
        return self.on_sale() or self.unit_price is not None 
    
    def price(self):
        """
        Returns the actual price - sale price if applicable otherwise the unit 
        price.
        """
        if self.on_sale():
            return self.sale_price
        elif self.has_price():
            return self.unit_price
        return Decimal("0")

class Product(Displayable, Priced):
    """
    Container model for a product that stores information common to all of its 
    variations such as the product's title and description.
    """

    description = models.TextField(_("Description"), blank=True)
    keywords = models.CharField(_("Keywords"), max_length=200, blank=True, 
        null=True)
    available = models.BooleanField(_("Available for purchase"), default=False)
    image = models.CharField(max_length=100, blank=True, null=True)
    categories = models.ManyToManyField("Category", blank=True,
        related_name="products")
    date_added = models.DateTimeField(_("Date added"), auto_now_add=True, 
        null=True)

    objects = managers.ProductManager()
    search_fields = ("title", "description", "keywords")

    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")

    def copy_default_variation(self):
        """
        Copies the price and image fields from the default variation.
        """
        default = self.variations.get(default=True)
        for field in Priced._meta.fields:
            if not isinstance(field, models.AutoField):
                setattr(self, field.name, getattr(default, field.name))
        if default.image:
            self.image = default.image.file.name
        self.save()

    def admin_thumb(self):
        if self.image is None:
            return ""
        from cartridge.shop.templatetags.shop_tags import thumbnail
        thumb_url = "%s%s" % (settings.MEDIA_URL, thumbnail(self.image, 24, 24))
        return "<img src='%s' />" % thumb_url
    admin_thumb.allow_tags = True
    admin_thumb.short_description = ""

class ProductImage(models.Model):
    """
    An image for a product - a relationship is also defined with the product's 
    variations so that each variation can potentially have it own image, while 
    the relationship between the Product and ProductImage models ensures 
    there is a single set of images for the product.
    """
    
    file = models.ImageField(_("Image"), upload_to="product")
    description = models.CharField(_("Description"), blank=True, max_length=100)
    product = models.ForeignKey("Product", related_name="images")
    
    class Meta:
        verbose_name = _("Image")
        verbose_name_plural = _("Images")
    
    def __unicode__(self):
        return self.description if self.description else self.file.name

class ProductVariationMetaclass(ModelBase):
    """
    Metaclass for the ProductVariation model that dynamcally assigns an 
    OptionField for each option in shop.settings.PRODUCT_OPTIONS.
    """
    def __new__(cls, name, bases, attrs):
        for option in OPTION_TYPE_CHOICES:
            attrs["option%s" % option[0]] = OptionField(option[1])
        return super(ProductVariationMetaclass, cls).__new__(cls, name, bases, 
            attrs)

class ProductVariation(Priced):
    """
    A combination of selected options from cartridge.shop.settings.PRODUCT_OPTIONS for a 
    Product instance.
    """
    
    product = models.ForeignKey("Product", related_name="variations")
    sku = SKUField(unique=True)
    num_in_stock = models.IntegerField(_("Number in stock"), blank=True, 
        null=True) 
    default = models.BooleanField(_("Default"))
    image = models.ForeignKey("ProductImage", null=True, blank=True)

    objects = managers.ProductVariationManager()

    __metaclass__ = ProductVariationMetaclass

    class Meta:
        ordering = ("default",)
        
    def __unicode__(self):
        """
        Display the option names and values for the variation.
        """
        options = ", ".join(["%s: %s" % (unicode(f.verbose_name), getattr(self, f.name)) 
            for f in self.option_fields() if getattr(self, f.name) is not None])
        return ("%s %s" % (self.product, options)).strip()
    
    def save(self, *args, **kwargs):
        """
        Use the variation's ID as the SKU when the variation is first created 
        and set the variation's image to be the first image of the product if 
        no image is chosen for the variation.
        """
        super(ProductVariation, self).save(*args, **kwargs)
        save = False
        if not self.sku:
            self.sku = self.id
            save = True
        if not self.image:
            image = self.product.images.all()[:1]
            if len(image) == 1:
                self.image = image[0]
                save = True
        if save:
            self.save()

    def get_absolute_url(self):
        return self.product.get_absolute_url()

    @classmethod
    def option_fields(cls):
        return [field for field in cls._meta.fields 
            if isinstance(field, OptionField)]
    
    def options(self):
        return [getattr(self, field.name) for field in self.option_fields()]

    def has_stock(self, quantity=1):
        """
        Check the given quantity is in stock taking into account the number in 
        carts, and cache the number.
        """
        if self.num_in_stock is None or quantity == 0:
            return True
        if not hasattr(self, "_cached_num_in_stock"):
            num_in_stock = self.num_in_stock
            num_in_carts = CartItem.objects.filter(sku=self.sku).aggregate(
                quantity_sum=models.Sum("quantity"))["quantity_sum"]
            if num_in_carts is not None:
                num_in_stock = num_in_stock - num_in_carts
            self._cached_num_in_stock = num_in_stock
        return self._cached_num_in_stock >= quantity

class Order(models.Model):

    billing_detail_first_name = models.CharField(_("First name"), 
        max_length=100)
    billing_detail_last_name = models.CharField(_("Last name"), max_length=100)
    billing_detail_street = models.CharField(_("Street"), max_length=100)
    billing_detail_city = models.CharField(_("City/Suburb"), max_length=100)
    billing_detail_state = models.CharField(_("State/Region"), max_length=100)
    billing_detail_postcode = models.CharField(_("Zip/Postcode"), max_length=10)
    billing_detail_country = models.CharField(_("Country"), max_length=100)
    billing_detail_phone = models.CharField(_("Phone"), max_length=20)
    billing_detail_email = models.EmailField(_("Email"))
    shipping_detail_first_name = models.CharField(_("First name"), 
        max_length=100)
    shipping_detail_last_name = models.CharField(_("Last name"), max_length=100)
    shipping_detail_street = models.CharField(_("Street"), max_length=100)
    shipping_detail_city = models.CharField(_("City/Suburb"), max_length=100)
    shipping_detail_state = models.CharField(_("State/Region"), max_length=100)
    shipping_detail_postcode = models.CharField(_("Zip/Postcode"), 
        max_length=10)
    shipping_detail_country = models.CharField(_("Country"), max_length=100)
    shipping_detail_phone = models.CharField(_("Phone"), max_length=20)
    additional_instructions = models.TextField(_("Additional instructions"), 
        blank=True)
    time = models.DateTimeField(_("Time"), auto_now_add=True, null=True)
    key = models.CharField(max_length=40)
    user_id = models.IntegerField(blank=True, null=True)
    shipping_type = models.CharField(_("Shipping type"), max_length=50, 
        blank=True)
    shipping_total = MoneyField(_("Shipping total"))
    item_total = MoneyField(_("Item total"))
    discount_code = DiscountCodeField(_("Discount code"), blank=True)
    discount_total = MoneyField(_("Discount total"))
    total = MoneyField(_("Order total"))
    status = models.IntegerField(_("Status"), choices=ORDER_STATUS_CHOICES, 
        default=ORDER_STATUS_CHOICES[0][0])

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ("-id",)

    def __unicode__(self):
        return "#%s %s %s" % (self.id, self.billing_name(), self.time)

    def save(self, *args, **kwargs):
        self.total = self.item_total
        if self.shipping_total is not None:
            self.total += self.shipping_total
        if self.discount_total is not None:
            self.total -= self.discount_total
        super(Order, self).save(*args, **kwargs)

    def billing_name(self):
        return "%s %s" % (self.billing_detail_first_name, 
            self.billing_detail_last_name)

    def process(self, request):
        """
        Process a successful order.
        """
        cart = Cart.objects.from_request(request)
        # Get fields fields from session and remove order details from session.
        for field in ("shipping_type", "shipping_total", "discount_total"):
            if field in request.session:
                setattr(self, field, request.session[field])
                del request.session[field]
        del request.session["order"]
        # Set final fields and save.
        self.item_total = cart.total_price()
        self.key = request.session.session_key
        self.user_id = request.user.id    
        self.save()
        # Copy items from cart and delete the cart.
        for item in cart:
            try:
                variation = ProductVariation.objects.get(sku=item.sku)
            except ProductVariation.DoesNotExist:
                pass
            else:
                if variation.num_in_stock is not None:
                    variation.num_in_stock -= item.quantity
                    variation.save()
                variation.product.actions.purchased()
            fields = [f.name for f in SelectedProduct._meta.fields]
            item = dict([(f, getattr(item, f)) for f in fields])
            self.items.create(**item)
        cart.delete()

class Cart(models.Model):

    last_updated = models.DateTimeField(_("Last updated"), auto_now=True, 
        null=True)

    objects = managers.CartManager()

    def __iter__(self):
        """
        allow the cart to be iterated giving access to the cart's items, 
        ensuring the items are only retrieved once and cached
        """
        if not hasattr(self, "_cached_items"):
            self._cached_items = self.items.all()
        return iter(self._cached_items)
        
    def add_item(self, variation, quantity):
        """
        increase quantity of existing item if sku matches, otherwise create new
        """
        item, created = self.items.get_or_create(sku=variation.sku, 
            unit_price=variation.price())
        if created:
            item.description = str(variation)
            item.unit_price = variation.price()
            item.url = variation.product.get_absolute_url()
            image = variation.image
            if image is not None:
                item.image = str(image)
            variation.product.actions.added_to_cart()
        item.quantity += quantity
        item.save()
            
    def remove_item(self, item_id):
        """
        remove item by sku
        """
        try:
            self.items.get(id=item_id).delete()
        except CartItem.DoesNotExist:
            pass
        
    def has_items(self):
        """
        template helper function - does the cart have items
        """
        return len(list(self)) > 0 
    
    def total_quantity(self):
        """
        template helper function - sum of all item quantities
        """
        return sum([item.quantity for item in self])
        
    def total_price(self):
        """
        template helper function - sum of all costs of item quantities
        """
        return sum([item.total_price for item in self])
    
class SelectedProduct(models.Model):
    """
    abstract model representing a "selected" product in a cart or order
    """

    sku = SKUField()
    description = models.CharField(_("Description"), max_length=200)
    quantity = models.IntegerField(_("Quantity"), default=0)
    unit_price = MoneyField(_("Unit price"), default=Decimal("0"))
    total_price = MoneyField(_("Total price"), default=Decimal("0"))
    
    class Meta:
        abstract = True
        
    def __unicode__(self):
        return ""
    
    def save(self, *args, **kwargs):
        self.total_price = self.unit_price * self.quantity
        super(SelectedProduct, self).save(*args, **kwargs)

class CartItem(SelectedProduct):

    cart = models.ForeignKey("Cart", related_name="items")
    url = models.CharField(max_length=200)
    image = models.CharField(max_length=200, null=True)
    
    def get_absolute_url(self):
        return self.url

class OrderItem(SelectedProduct):
    """
    A selected product in a completed order.
    """
    order = models.ForeignKey("Order", related_name="items")

class ProductAction(models.Model):
    """
    Records an incremental value for an action against a product such as adding 
    to cart or purchasing, for sales reporting and calculating popularity. Not 
    yet used but will be used for product popularity and sales reporting.
    """
        
    product = models.ForeignKey("Product", related_name="actions")
    timestamp = models.IntegerField()
    total_cart = models.IntegerField(default=0)
    total_purchase = models.IntegerField(default=0)

    objects = managers.ProductActionManager()

    class Meta:
        unique_together = ("product", "timestamp")

class Discount(models.Model):
    """
    Abstract model representing one of several types of monetary reductions as 
    well as a date range they're applicable for, and the products and products 
    in categories that the reduction is applicable for.
    """

    title = models.CharField(max_length=100)
    active = models.BooleanField(_("Active"))
    products = models.ManyToManyField("Product", blank=True)
    categories = models.ManyToManyField("Category", blank=True)
    discount_deduct = MoneyField(_("Reduce by amount"))
    discount_percent = models.DecimalField(_("Reduce by percent"), max_digits=4, 
        decimal_places=2, blank=True, null=True)
    discount_exact = MoneyField(_("Reduce to amount"))
    valid_from = models.DateTimeField(_("Valid from"), blank=True, null=True)
    valid_to = models.DateTimeField(_("Valid to"), blank=True, null=True)

    class Meta:
        abstract = True

    def __unicode__(self):
        return self.title
        
    def all_products(self):
        """
        return the selected products as well as the products in the selected 
        categories
        """
        return Product.objects.filter(models.Q(id__in=self.products.all()) | 
            models.Q(categories__in=self.categories.all()))

class Sale(Discount):
    """
    stores sales field values for price and date range which when saved are 
    then applied across products and variations according to the selected 
    categories and products for the sale
    """
    
    class Meta:
        verbose_name = _("Sale")
        verbose_name_plural = _("Sales")
    
    def save(self, *args, **kwargs):
        """
        apply sales field value to products and variations according to the 
        selected categories and products for the sale 
        """
        super(Sale, self).save(*args, **kwargs)
        self._clear()
        if self.active:
            extra_filter = {}
            if self.discount_deduct is not None:
                # don't apply to prices that would be negative after deduction
                extra_filter["unit_price__gt"] = self.discount_deduct
                sale_price = models.F("unit_price") - self.discount_deduct
            elif self.discount_percent is not None:
                sale_price = models.F("unit_price") - (models.F("unit_price") / 
                    "100.0" * self.discount_percent)
            elif self.discount_exact is not None:
                # don't apply to prices that are cheaper than the sale amount
                extra_filter["unit_price__gt"] = self.discount_exact
                sale_price = self.discount_exact
            else:
                return
            products = self.all_products()
            variations = ProductVariation.objects.filter(product__in=products)
            for priced_objects in (products, variations):
                priced_objects.filter(**extra_filter).update(sale_id=self.id, 
                    sale_to=self.valid_to, sale_from=self.valid_from, 
                    sale_price=sale_price)
    
    def delete(self, *args, **kwargs):
        self._clear()
        super(Sale, self).delete(*args, **kwargs)

    def _clear(self):
        """
        clears previously applied sale field values from products prior to 
        updating the sale, when deactivating it or deleting it
        """
        for priced_model in (Product, ProductVariation):
            priced_model.objects.filter(sale_id=self.id).update(sale_id=None, 
                sale_from=None, sale_to=None, sale_price=None)
    
class DiscountCode(Discount):
    """
    a code that can be entered at the checkout process to have a discount 
    applied to the total purchase amount
    """
    
    code = DiscountCodeField(_("Code"), unique=True)
    min_purchase = MoneyField(_("Minimum total purchase"))
    free_shipping = models.BooleanField(_("Free shipping"))

    objects = managers.DiscountCodeManager()
    
    def calculate(self, amount):
        """
        calculates the discount for the given amount
        """
        if self.discount_deduct is not None:
            # don't apply to amounts that would be negative after deduction
            if self.discount_deduct < amount:
                return self.discount_deduct
        elif self.discount_percent is not None:
            return amount / Decimal("100") * self.discount_percent
        return 0
