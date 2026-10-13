import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Product, Inventory, Dealer


class Command(BaseCommand):
    help = "Load catalogue.csv and dealers.csv into the database idempotently. Safe to run multiple times."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR

        cat_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not cat_path.exists():
            self.stderr.write(self.style.ERROR(f"Catalogue file not found at {cat_path}"))
            return

        if not dealers_path.exists():
            self.stderr.write(self.style.ERROR(f"Dealers file not found at {dealers_path}"))
            return

        self.stdout.write("Seeding products and inventory from catalogue.csv...")
        
        products_to_create = []
        stock_map = {}
        with open(cat_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row["sku"].strip()
                products_to_create.append(Product(
                    sku=sku,
                    name=row["name"].strip(),
                    category=row["category"].strip(),
                    brand=row["brand"].strip(),
                    vehicle_fitment=row["vehicle_fitment"].strip(),
                    price=Decimal(row["price"].strip()),
                    description=row["description"].strip(),
                ))
                stock_map[sku] = int(row["stock"].strip())

        with transaction.atomic():
            Product.objects.bulk_create(
                products_to_create,
                update_conflicts=True,
                unique_fields=["sku"],
                update_fields=["name", "category", "brand", "vehicle_fitment", "price", "description"],
            )

            all_products = {p.sku: p for p in Product.objects.filter(sku__in=list(stock_map.keys()))}
            inventories_to_create = [
                Inventory(
                    product=all_products[sku],
                    quantity=stock_qty,
                    reason="Initial catalogue seed",
                )
                for sku, stock_qty in stock_map.items()
                if sku in all_products
            ]

            Inventory.objects.bulk_create(
                inventories_to_create,
                update_conflicts=True,
                unique_fields=["product"],
                update_fields=["quantity", "reason"],
            )

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(products_to_create)} products and inventory records."))

        self.stdout.write("Seeding dealers from dealers.csv...")
        dealers_to_create = []
        with open(dealers_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dealers_to_create.append(Dealer(
                    dealer_code=row["dealer_code"].strip(),
                    name=row["name"].strip(),
                    city=row.get("city", "").strip(),
                    phone=row.get("phone", "").strip(),
                    email=row.get("email", "").strip(),
                    gstin=row.get("gstin", "").strip(),
                    credit_limit=Decimal(row.get("credit_limit", "0.00").strip() or "0.00"),
                ))

        with transaction.atomic():
            Dealer.objects.bulk_create(
                dealers_to_create,
                update_conflicts=True,
                unique_fields=["dealer_code"],
                update_fields=["name", "city", "phone", "email", "gstin", "credit_limit"],
            )

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(dealers_to_create)} dealers."))
        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully."))
