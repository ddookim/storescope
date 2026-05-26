import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/storescope_test")
os.environ.setdefault("ADMIN_SECRET", "test_admin_secret_min_32_chars_here")
os.environ.setdefault("PADDLE_WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("PADDLE_PRO_PRICE_ID", "pri_pro_test")
os.environ.setdefault("PADDLE_STARTER_PRICE_ID", "pri_starter_test")
