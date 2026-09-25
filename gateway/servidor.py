from .aplicacion import crear_aplicacion
from .configuracion import ConfiguracionPasarela

app = crear_aplicacion(ConfiguracionPasarela.desde_entorno())

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
