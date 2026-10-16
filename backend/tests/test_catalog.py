import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.catalog import db_session
from app.database import Base
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_db():
    with TestSession() as session:
        yield session


class CatalogTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[db_session] = test_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    def test_products_crud_and_duplicate_sku(self):
        payload = {"sku": "SARAE-001", "name": "Áo trễ vai", "price": 155000}
        result = self.client.post("/api/products", json=payload)
        self.assertEqual(result.status_code, 201, result.text)
        product_id = result.json()["id"]
        self.assertEqual(len(self.client.get("/api/products").json()), 1)
        self.assertEqual(self.client.post("/api/products", json=payload).status_code, 409)
        payload["name"] = "Áo trễ vai trắng"
        self.assertEqual(self.client.put(f"/api/products/{product_id}", json=payload).status_code, 200)
        self.assertEqual(self.client.get(f"/api/products/{product_id}").json()["name"], payload["name"])
        self.assertEqual(self.client.delete(f"/api/products/{product_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/products/{product_id}").status_code, 404)

    def test_presenters_crud(self):
        result = self.client.post("/api/presenters", json={"name": "Người giới thiệu A"})
        self.assertEqual(result.status_code, 201, result.text)
        presenter_id = result.json()["id"]
        self.assertEqual(len(self.client.get("/api/presenters?q=giới").json()), 1)
        self.assertEqual(self.client.put(f"/api/presenters/{presenter_id}", json={"name": "Người giới thiệu B", "status": "INACTIVE"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/presenters/{presenter_id}").status_code, 204)

    def test_validation(self):
        self.assertEqual(self.client.post("/api/products", json={"sku": "", "name": "A"}).status_code, 422)
        self.assertEqual(self.client.post("/api/presenters", json={"name": " "}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
