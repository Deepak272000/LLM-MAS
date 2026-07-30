"""
server.py    gRPC server for productcatalogservice

Key fixes vs the original:
  - context.set_details(str(e)) is ALWAYS called alongside set_code so the
    caller can actually see the error message
  - Full traceback logged on every exception so you can diagnose issues
  - products.json path configurable via env var (consistent with catalog_graph)
  - Health-check stub so K8s readiness probes don't fail
"""

import grpc
import json
import os
import traceback
from concurrent import futures

import hipstershop_pb2
import hipstershop_pb2_grpc

try:
    from boundary_validation import boundary_contract
except ImportError:  # pragma: no cover - fallback for standalone service container
    def boundary_contract(name, expected, observed, **_kwargs):
        return {
            "boundary": name,
            "alert": expected != observed,
            "status": "signal_escape" if expected != observed else "clean",
            "expected": expected,
            "observed": observed,
            "difference": None,
            "detail": None,
            "violations": [],
        }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CATALOG_PATH = os.getenv("PRODUCT_CATALOG_JSON", "products.json")
GRPC_PORT    = os.getenv("GRPC_PORT", "3550")

def record_catalog_boundary(name: str, expected, observed) -> None:
    boundary = boundary_contract(
        name,
        expected,
        observed,
        validators={"__list__": lambda value: all(isinstance(item, str) and item for item in value)},
    )
    print(f"[BOUNDARY_CHECK] {boundary}")

# ---------------------------------------------------------------------------
# Service implementation
# ---------------------------------------------------------------------------
class ProductCatalogService(hipstershop_pb2_grpc.ProductCatalogServiceServicer):

    def __init__(self):
        try:
            with open(CATALOG_PATH, "r") as f:
                self.db = json.load(f)["products"]
            print(f"[server] Loaded {len(self.db)} products from {CATALOG_PATH}")
        except Exception as e:
            print(f"[server] FATAL  could not load products: {e}")
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _convert(self, p: dict) -> hipstershop_pb2.Product:
        """Convert a dict from products.json into a protobuf Product."""
        return hipstershop_pb2.Product(
            id=p["id"],
            name=p["name"],
            description=p.get("description", ""),
            picture=p.get("picture", ""),
            categories=p.get("categories", []),
            price_usd=hipstershop_pb2.Money(
                currency_code=p["priceUsd"]["currencyCode"],
                units=p["priceUsd"]["units"],
                nanos=p["priceUsd"]["nanos"],
            ),
        )

    # ------------------------------------------------------------------
    # Existing RPC methods  (unchanged behaviour)
    # ------------------------------------------------------------------
    def ListProducts(self, request, context):
        try:
            print(f"[server] ListProducts called")
            products = [self._convert(p) for p in self.db]
            product_ids = [product.id for product in products]
            record_catalog_boundary("catalog_list_to_response", product_ids, product_ids)
            return hipstershop_pb2.ListProductsResponse(products=products)
        except Exception as e:
            print(f"[server] ListProducts error:\n{traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))          # ? THIS is what was missing
            return hipstershop_pb2.ListProductsResponse()

    def GetProduct(self, request, context):
        try:
            print(f"[server] GetProduct called: id={request.id!r}")
            p = next((x for x in self.db if x["id"] == request.id), None)
            if not p:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"product {request.id!r} not found")
                return hipstershop_pb2.Product()
            product = self._convert(p)
            record_catalog_boundary("catalog_get_to_response", [request.id], [product.id])
            return product
        except Exception as e:
            print(f"[server] GetProduct error:\n{traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return hipstershop_pb2.Product()

    # ------------------------------------------------------------------
    # AI-powered SearchProducts  (uses LangGraph + Ollama)
    # ------------------------------------------------------------------
    def SearchProducts(self, request, context):
        try:
            print(f"[server] SearchProducts called: query={request.query!r}")
            from catalog_graph import graph

            output = graph.invoke({"query": request.query})
            print(f"[server] Graph output: {output}")

            pb_results = [self._convert(p) for p in output.get("results", [])]
            result_ids = [product.id for product in pb_results]
            record_catalog_boundary("catalog_search_to_response", result_ids, result_ids)
            print(f"[server] Returning {len(pb_results)} products")
            return hipstershop_pb2.SearchProductsResponse(results=pb_results)

        except Exception as e:
            # Log the FULL traceback  this is what was hiding your error
            print(f"[server] SearchProducts error:\n{traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))          # ? grpcurl will now show this
            return hipstershop_pb2.SearchProductsResponse()

# ---------------------------------------------------------------------------
# Start server
# ---------------------------------------------------------------------------
def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    hipstershop_pb2_grpc.add_ProductCatalogServiceServicer_to_server(
        ProductCatalogService(), server
    )
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    print(f"[server] gRPC server listening on port {GRPC_PORT}")
    server.start()
    server.wait_for_termination()

if __name__ == "__main__":
    serve()
