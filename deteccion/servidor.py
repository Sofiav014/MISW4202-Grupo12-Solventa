from .aplicacion import crear_aplicacion
from .configuracion import ConfiguracionDeteccion

app = crear_aplicacion(ConfiguracionDeteccion.desde_entorno())


@app.get("/healthz")
def healthz():
    """Endpoint de salud usado por el healthcheck de Docker Compose."""
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
