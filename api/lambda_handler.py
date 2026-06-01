from mangum import Mangum

from api.app import app

# AWS Lambda entry point.
# Mangum adapts FastAPI (ASGI) to Lambda's event/context interface.
handler = Mangum(app, lifespan="on")
