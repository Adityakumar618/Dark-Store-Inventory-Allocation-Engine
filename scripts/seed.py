"""
Dark-Store Inventory Allocation Engine: Realistic Synthetic Seeding Script
File: scripts/seed.py
Description: Generates 20 dark stores, 200 realistic quick-commerce SKUs,
             multi-batch inventory with expiration schedules, customers,
             riders, and initial baseline orders.
"""

import os
import sys
import random
import datetime
import psycopg
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "darkstore_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgrespassword")

BANGALORE_ZONES = [
    ("Indiranagar", "560038", 12.9719, 77.6412),
    ("Koramangala", "560034", 12.9352, 77.6245),
    ("HSR Layout", "560102", 12.9121, 77.6446),
    ("Whitefield", "560066", 12.9698, 77.7500),
    ("Bellandur", "560103", 12.9260, 77.6762),
    ("Jayanagar", "560041", 12.9308, 77.5838),
    ("Marathahalli", "560037", 12.9592, 77.6974),
    ("Malleshwaram", "560003", 13.0031, 77.5643),
    ("JP Nagar", "560078", 12.9063, 77.5857),
    ("Electronic City", "560100", 12.8452, 77.6602),
    ("BTM Layout", "560076", 12.9166, 77.6101),
    ("Hebbal", "560024", 13.0358, 77.5970),
    ("Rajajinagar", "560010", 12.9982, 77.5530),
    ("Banashankari", "560085", 12.9255, 77.5468),
    ("Sarjapur Road", "560035", 12.9100, 77.6850),
    ("Domlur", "560071", 12.9609, 77.6387),
    ("Yelahanka", "560064", 13.1007, 77.5963),
    ("Kalyan Nagar", "560043", 13.0280, 77.6392),
    ("Basavanagudi", "560004", 12.9416, 77.5755),
    ("Frazer Town", "560005", 12.9968, 77.6130),
]

SKU_CATALOG = [
    # Dairy & Breakfast
    ("Amul Taaza Homogenised Toned Milk 500ml", "Dairy & Breakfast", True, 28.00, 15),
    ("Nandini GoodLife Milk 500ml", "Dairy & Breakfast", True, 30.00, 15),
    ("Country Delight Pure Cow Milk 1L", "Dairy & Breakfast", True, 75.00, 10),
    ("Epigamia Greek Yogurt Strawberry 90g", "Dairy & Breakfast", True, 50.00, 8),
    ("Amul Salted Butter 100g", "Dairy & Breakfast", True, 56.00, 12),
    ("Britannia Cheese Slices 200g (10 Slices)", "Dairy & Breakfast", True, 140.00, 10),
    ("Mother Dairy Classic Dahi 400g", "Dairy & Breakfast", True, 35.00, 15),
    ("English Oven Whole Wheat Bread 400g", "Dairy & Breakfast", True, 45.00, 12),
    ("Nestle Milkmaid Sweetened Condensed Milk 380g", "Dairy & Breakfast", False, 144.00, 5),
    ("Eggoz Farm Fresh Brown Eggs (Pack of 6)", "Dairy & Breakfast", True, 65.00, 10),

    # Fresh Produce (Fruits & Vegetables)
    ("Fresh Cavendish Bananas (500g)", "Fresh Produce", True, 35.00, 20),
    ("Shimla Royal Delicious Apple 4 pcs (500g)", "Fresh Produce", True, 120.00, 15),
    ("Fresh Hybrid Tomato 1kg", "Fresh Produce", True, 32.00, 25),
    ("Nashik Red Onion 1kg", "Fresh Produce", True, 45.00, 25),
    ("Fresh Baby Potato 1kg", "Fresh Produce", True, 38.00, 20),
    ("Farm Fresh Coriander Leaves 100g", "Fresh Produce", True, 15.00, 30),
    ("Fresh Green Chillies 100g", "Fresh Produce", True, 12.00, 30),
    ("Fresh Lemon 4 pcs", "Fresh Produce", True, 25.00, 25),
    ("Fresh Ginger (Adrak) 100g", "Fresh Produce", True, 18.00, 20),
    ("Green Seedless Grapes 500g", "Fresh Produce", True, 85.00, 12),

    # Beverages & Drinks
    ("Coca-Cola Original Taste 750ml", "Beverages", False, 40.00, 20),
    ("Thums Up Charged 750ml", "Beverages", False, 40.00, 20),
    ("Red Bull Energy Drink 250ml", "Beverages", False, 125.00, 15),
    ("Raw Pressery Valencia Orange Juice 200ml", "Beverages", True, 60.00, 10),
    ("Bisleri Mineral Water 1L", "Beverages", False, 20.00, 30),
    ("Sprite Lime Flavored Drink 750ml", "Beverages", False, 40.00, 20),
    ("Paper Boat Tender Coconut Water 200ml", "Beverages", True, 50.00, 15),
    ("Nescafe Classic Instant Coffee 50g Jar", "Beverages", False, 180.00, 8),
    ("Tata Tea Gold 500g Pouch", "Beverages", False, 310.00, 8),
    ("Real Fruit Power Mixed Fruit Juice 1L", "Beverages", False, 115.00, 10),

    # Snacks & Instant Food
    ("Maggi 2-Minute Masala Instant Noodles 70g", "Snacks & Instant", False, 14.00, 50),
    ("Lay's India's Magic Masala Potato Chips 50g", "Snacks & Instant", False, 20.00, 40),
    ("Haldiram's Nagpur Aloo Bhujia 200g", "Snacks & Instant", False, 55.00, 25),
    ("Kurkure Masala Munch Crisps 85g", "Snacks & Instant", False, 20.00, 40),
    ("Doritos Cheese Supreme Nachos 60g", "Snacks & Instant", False, 30.00, 30),
    ("Cadbury Dairy Milk Silk Chocolate 60g", "Snacks & Instant", False, 80.00, 20),
    ("Parle-G Gold Biscuits 1kg", "Snacks & Instant", False, 90.00, 15),
    ("Sunfeast Dark Fantasy Choco Fills 300g", "Snacks & Instant", False, 120.00, 15),
    ("Kissan Fresh Tomato Ketchup 950g", "Snacks & Instant", False, 130.00, 10),
    ("Ching's Secret Schezwan Chutney 250g", "Snacks & Instant", False, 85.00, 12),

    # Staples & Cooking Essentials
    ("Aashirvaad Superior MP Shudh Chakki Atta 5kg", "Staples", False, 245.00, 15),
    ("Fortune Sunlite Refined Sunflower Oil 1L", "Staples", False, 135.00, 20),
    ("Tata Salt Vaccum Evaporated Iodized Salt 1kg", "Staples", False, 28.00, 30),
    ("India Gate Super Basmati Rice 1kg", "Staples", False, 175.00, 12),
    ("Tata Sampann Unpolished Toor Dal 1kg", "Staples", False, 180.00, 15),
    ("Saffola Gold Pro Healthy Lifestyle Oil 1L", "Staples", False, 165.00, 15),
    ("Madhur Pure & Hygienic Sugar 1kg", "Staples", False, 52.00, 25),
    ("MDH Deggi Mirch Powder 100g", "Staples", False, 88.00, 15),
    ("Catch Super Garam Masala 100g", "Staples", False, 76.00, 15),
    ("Everest Turmeric Powder 100g", "Staples", False, 38.00, 20),

    # Personal Care & Cleaning
    ("Dettol Original Liquid Handwash Refill 675ml", "Personal Care & Home", False, 99.00, 15),
    ("Vim Lemon Dishwash Gel 500ml Bottle", "Personal Care & Home", False, 110.00, 20),
    ("Surf Excel Matic Top Load Liquid Detergent 1L", "Personal Care & Home", False, 220.00, 10),
    ("Colgate Total Antibacterial Toothpaste 150g", "Personal Care & Home", False, 125.00, 15),
    ("Head & Shoulders Anti-Dandruff Shampoo 180ml", "Personal Care & Home", False, 170.00, 12),
    ("Harpic Disinfectant Toilet Cleaner 500ml", "Personal Care & Home", False, 95.00, 20),
    ("Origami So Soft 3-Ply Toilet Tissue Rolls (4 Rolls)", "Personal Care & Home", False, 140.00, 10),
    ("Savlon Moisture Shield Germ Protection Soap (Pack of 4)", "Personal Care & Home", False, 135.00, 15),
    ("Comfort After Wash Fabric Conditioner Lily 860ml", "Personal Care & Home", False, 215.00, 8),
    ("Goodknight Gold Flash Mosquito Liquid Refill (45ml)", "Personal Care & Home", False, 82.00, 25),
]

# Expand catalog to 200 items programmatically with realistic brand/pack variants
def generate_expanded_skus():
    skus = list(SKU_CATALOG)
    categories = ["Dairy & Breakfast", "Fresh Produce", "Beverages", "Snacks & Instant", "Staples", "Personal Care & Home"]
    pack_sizes = ["Small Pack", "Family Pack", "Combo 2-Pack", "Economy Pack", "Value Pack", "Special Edition"]
    
    idx = len(skus) + 1
    while len(skus) < 200:
        base_item = random.choice(SKU_CATALOG)
        variant_pack = random.choice(pack_sizes)
        multiplier = round(random.uniform(0.7, 2.5), 2)
        new_name = f"{base_item[0].split('(')[0].strip()} ({variant_pack} {idx})"
        new_price = round(float(base_item[3]) * multiplier, 2)
        new_threshold = random.randint(5, 25)
        skus.append((new_name, base_item[1], base_item[2], new_price, new_threshold))
        idx += 1
    return skus

def seed_database():
    conn_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
    print(f"[*] Connecting to PostgreSQL at {DB_HOST}:{DB_PORT}/{DB_NAME}...")
    
    try:
        with psycopg.connect(conn_str, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("[*] Executing Schema DDL & Triggers...")
                with open("schema/001_init.sql", "r") as f:
                    cur.execute(f.read())
                with open("schema/002_triggers.sql", "r") as f:
                    cur.execute(f.read())
                
                print("[+] Seeding 20 Delivery Zones & Dark Stores...")
                zone_ids = []
                for name, pincode, lat, lng in BANGALORE_ZONES:
                    cur.execute(
                        "INSERT INTO zones (name, pincode, city) VALUES (%s, %s, %s) RETURNING zone_id",
                        (name, pincode, "Bangalore")
                    )
                    zid = cur.fetchone()[0]
                    zone_ids.append((zid, name, lat, lng))
                    
                    # Create corresponding Dark Store
                    cur.execute(
                        """
                        INSERT INTO stores (name, zone_id, lat, lng, is_active)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (f"Zepto/Blinkit Dark Store - {name}", zid, lat, lng, True)
                    )
                
                print("[+] Seeding 200 SKUs...")
                full_skus = generate_expanded_skus()
                sku_ids = []
                for name, category, is_perishable, price, threshold in full_skus:
                    cur.execute(
                        """
                        INSERT INTO skus (name, category, is_perishable, price, reorder_threshold)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING sku_id, is_perishable
                        """,
                        (name, category, is_perishable, price, threshold)
                    )
                    sku_ids.append(cur.fetchone())

                print("[+] Seeding Batches (with FEFO expiry dates) and Multi-Store Inventory...")
                today = datetime.date.today()
                
                # Fetch all created store IDs
                cur.execute("SELECT store_id FROM stores")
                store_rows = cur.fetchall()
                store_ids = [r[0] for r in store_rows]

                for sku_id, is_perishable in sku_ids:
                    # Create 2 to 4 batches per SKU with varied expiry dates
                    num_batches = random.randint(2, 4)
                    for batch_num in range(1, num_batches + 1):
                        if is_perishable:
                            # Some near-expiry (2 to 5 days) for alerting, some fresh (10 to 30 days)
                            if batch_num == 1 and random.random() < 0.3:
                                expiry = today + datetime.timedelta(days=random.randint(1, 3))
                            else:
                                expiry = today + datetime.timedelta(days=random.randint(5, 30))
                        else:
                            expiry = today + datetime.timedelta(days=random.randint(90, 365))
                        
                        received_at = datetime.datetime.now() - datetime.timedelta(days=random.randint(1, 10))
                        cur.execute(
                            """
                            INSERT INTO batches (sku_id, batch_id, expiry_date, received_at)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (sku_id, batch_num, expiry, received_at)
                        )

                        # Stock this batch across stores
                        for store_id in store_ids:
                            # 85% probability that store holds this batch
                            if random.random() < 0.85:
                                # Normal quantity 10-100, occasionally low (0-5) to test understocked view
                                qty = random.choice([0, 2, 4, random.randint(15, 80)])
                                cur.execute(
                                    """
                                    INSERT INTO inventory (store_id, sku_id, batch_id, qty_available)
                                    VALUES (%s, %s, %s, %s)
                                    """,
                                    (store_id, sku_id, batch_num, qty)
                                )

                print("[+] Seeding 50 Realistic Customers & 30 Delivery Fleet Riders...")
                first_names = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan",
                               "Diya", "Saanvi", "Ananya", "Aadhya", "Pari", "Chiara", "Myra", "Anvi", "Prisha", "Riya"]
                last_names = ["Sharma", "Verma", "Patel", "Reddy", "Rao", "Nair", "Iyer", "Hegde", "Menon", "Deshmukh",
                              "Kulkarni", "Gupta", "Agarwal", "Bose", "Chatterjee", "Mehta", "Joshi", "Bhat", "Kumar", "Singh"]

                customer_ids = []
                for i in range(1, 51):
                    zid, zname, zlat, zlng = random.choice(zone_ids)
                    cust_name = f"{random.choice(first_names)} {random.choice(last_names)}"
                    phone = f"98{random.randint(10000000, 99999999)}"
                    email = f"{cust_name.lower().replace(' ', '.')}{i}@example.com"
                    clat = zlat + random.uniform(-0.015, 0.015)
                    clng = zlng + random.uniform(-0.015, 0.015)
                    
                    cur.execute(
                        """
                        INSERT INTO customers (name, phone, email, zone_id, lat, lng)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING customer_id
                        """,
                        (cust_name, phone, email, zid, clat, clng)
                    )
                    customer_ids.append(cur.fetchone()[0])

                for i in range(1, 31):
                    zid, zname, zlat, zlng = random.choice(zone_ids)
                    rider_name = f"Rider {random.choice(first_names)} {random.choice(last_names)}"
                    phone = f"97{random.randint(10000000, 99999999)}"
                    status = random.choice(["IDLE", "IDLE", "DELIVERING", "ASSIGNED", "OFFLINE"])
                    rlat = zlat + random.uniform(-0.01, 0.01)
                    rlng = zlng + random.uniform(-0.01, 0.01)

                    cur.execute(
                        """
                        INSERT INTO riders (name, phone, current_zone_id, status, current_lat, current_lng)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (rider_name, phone, zid, status, rlat, rlng)
                    )

                print("[+] Seeding Historical Completed Orders for Analytical Views...")
                for order_idx in range(1, 40):
                    cid = random.choice(customer_ids)
                    idemp_key = f"HIST-ORD-{order_idx}-{random.randint(1000,9999)}"
                    order_status = random.choice(["ALLOCATED", "ALLOCATED", "ALLOCATED", "SPLIT_ALLOCATED", "FAILED"])
                    
                    cur.execute(
                        """
                        INSERT INTO orders (customer_id, status, idempotency_key, created_at, allocated_at, failure_reason)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING order_id
                        """,
                        (
                            cid, 
                            order_status, 
                            idemp_key, 
                            datetime.datetime.now() - datetime.timedelta(hours=random.randint(1, 48)),
                            datetime.datetime.now() - datetime.timedelta(hours=random.randint(1, 48)) if order_status != "FAILED" else None,
                            "Stock depleted mid-allocation" if order_status == "FAILED" else None
                        )
                    )
                    oid = cur.fetchone()[0]

                    # Order items
                    total_amount = 0.0
                    num_items = random.randint(1, 5)
                    selected_skus = random.sample(sku_ids, num_items)
                    store_allocated = random.choice(store_ids)

                    for s_id, is_per in selected_skus:
                        qty = random.randint(1, 4)
                        cur.execute("SELECT price FROM skus WHERE sku_id = %s", (s_id,))
                        price = float(cur.fetchone()[0])
                        item_status = "ALLOCATED" if order_status in ["ALLOCATED", "SPLIT_ALLOCATED"] else "UNFULFILLED"
                        
                        cur.execute(
                            """
                            INSERT INTO order_items (order_id, sku_id, qty_requested, store_id_allocated, batch_id_allocated, unit_price, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (oid, s_id, qty, store_allocated if item_status == "ALLOCATED" else None, 1 if item_status == "ALLOCATED" else None, price, item_status)
                        )
                        total_amount += (qty * price)

                    cur.execute("UPDATE orders SET total_amount = %s WHERE order_id = %s", (total_amount, oid))

                print("[*] Creating Analytical SQL Views...")
                with open("sql/views.sql", "r") as f:
                    cur.execute(f.read())

                print("\n[SUCCESS] Seed process completed successfully!")
                print(f"  - Zones: {len(BANGALORE_ZONES)}")
                print(f"  - Stores: {len(BANGALORE_ZONES)}")
                print(f"  - SKUs: {len(full_skus)}")
                print(f"  - Customers: 50")
                print(f"  - Fleet Riders: 30")
                print(f"  - Historical Orders: 39")

    except Exception as e:
        print(f"\n[ERROR] Failed to seed database: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    seed_database()
