"""
Generador de carga del experimento de seguridad (HA16).

Envía peticiones al Gateway con sesiones legítimas y suplantadas, y anota cada
una junto con lo que debería haberle ocurrido. El orquestador
(`generador.run_escenario`) lo invoca en modo headless y le pasa la
configuración por variables de entorno.

Se puede ejecutar a mano para depurar:

    ESCENARIO=B EJECUCION_ID=manual DESTINO_REGISTRO=/tmp/p.jsonl \\
      locust -f generador/locustfile.py --headless --host http://127.0.0.1:8080 \\
      -u 2 -r 2 -t 10s
"""

import os
from pathlib import Path
import random
import time
import uuid

from locust import HttpUser, between, events, task

from generador.escenarios import DISTANCIA_SUPLANTACION_KM, ESCENARIOS
from generador.poblacion import MODO_FRONTERA, MODO_UBICACION, construir
from generador.registro import Anotacion, RegistroPeticiones, clasificar
from generador.sembrar_identidad import CAMINO_CREDENCIALES_POR_DEFECTO, cargar
from parametros import SEMILLA_ALEATORIA


# Configuración recibida del orquestador. Se lee al importar, igual que en el
# experimento anterior, porque Locust instancia los usuarios después.
ESCENARIO = os.getenv("ESCENARIO", "A")
EJECUCION_ID = os.getenv("EJECUCION_ID", "manual")
DESTINO_REGISTRO = os.getenv("DESTINO_REGISTRO", "")
CAMINO_CREDENCIALES = Path(os.getenv("CAMINO_CREDENCIALES", str(CAMINO_CREDENCIALES_POR_DEFECTO)))
USUARIOS_CONFIGURADOS = int(os.getenv("USUARIOS_CONFIGURADOS", "0"))
RUTA_PROTEGIDA = os.getenv("RUTA_PROTEGIDA", "/api/journey/compra")

ESPERA_MIN_S = float(os.getenv("ESPERA_MIN_S", "0.1"))
ESPERA_MAX_S = float(os.getenv("ESPERA_MAX_S", "0.5"))

_escenario = ESCENARIOS[ESCENARIO]
_credenciales = cargar(CAMINO_CREDENCIALES)

# El registro es el artefacto del que sale la matriz de confusión; si no se
# indicó destino la corrida es descartable (warm-up) y no se anota nada.
_registro = RegistroPeticiones(Path(DESTINO_REGISTRO)) if DESTINO_REGISTRO else None

# Los modos de la mezcla y sus pesos, fijados una vez para no recalcularlos en
# cada petición.
_modos = list(_escenario.mezcla)
_pesos = [_escenario.mezcla[modo] for modo in _modos]

# Reparto de sesiones por distancia para la barrida de frontera.
#
# Cada distancia recibe sesiones propias porque el Detector reescribe la última
# ubicación conocida tras cada veredicto normal: si dos distancias compartieran
# sesión, la primera aceptada movería la línea base y la segunda se mediría
# contra un punto distinto del sembrado. Con sesiones separadas, cada bucket
# compara siempre contra la ubicación original.
_sesiones_por_distancia: dict[float, list] = {}

if _escenario.distancias_frontera:
    _distancias = list(_escenario.distancias_frontera)
    for _indice, _credencial in enumerate(_credenciales):
        _sesiones_por_distancia.setdefault(
            _distancias[_indice % len(_distancias)], []
        ).append(_credencial)


@events.quitting.add_listener
def _cerrar_registro(**_):
    """Cierra el registro al terminar la corrida."""
    if _registro is not None:
        _registro.cerrar()


class ClienteSolventa(HttpUser):
    """Un cliente que opera contra la ruta protegida del Gateway."""

    wait_time = between(ESPERA_MIN_S, ESPERA_MAX_S)

    def on_start(self):
        """Fija una semilla propia por usuario, reproducible entre corridas."""
        # Sumar el identificador del greenlet mantiene la reproducibilidad del
        # conjunto sin que todos los usuarios recorran la misma secuencia.
        self.aleatorio = random.Random(SEMILLA_ALEATORIA + id(self) % 100_000)

    def _elegir_muestra(self):
        """Construye la siguiente petición según la mezcla del escenario."""
        modo = self.aleatorio.choices(_modos, weights=_pesos, k=1)[0]

        if modo == MODO_FRONTERA:
            # Se sortea la distancia y luego una sesión de las asignadas a ella.
            distancia = self.aleatorio.choice(list(_sesiones_por_distancia))
            credencial = self.aleatorio.choice(_sesiones_por_distancia[distancia])
            return construir(credencial, modo, self.aleatorio, distancia_km=distancia)

        credencial = self.aleatorio.choice(_credenciales)
        distancia = DISTANCIA_SUPLANTACION_KM if modo == MODO_UBICACION else None

        return construir(credencial, modo, self.aleatorio, distancia_km=distancia)

    @task
    def operar(self):
        """Envía una petición al Gateway y anota su resultado."""
        muestra = self._elegir_muestra()
        request_id = str(uuid.uuid4())

        cabeceras = {
            "Authorization": f"Bearer {muestra.token}",
            # El contexto viaja serializado y con exactamente cinco campos: el
            # Gateway rechaza con 400 cualquier campo de más o de menos.
            "X-Session-Context": _serializar(muestra.contexto),
            "X-Request-ID": request_id,
            "Content-Type": "application/json",
        }

        status = None
        error_code = None
        fallo_transporte = None

        ts_envio = time.time()
        inicio = time.perf_counter()

        with self.client.post(
            RUTA_PROTEGIDA,
            data=b"{}",
            headers=cabeceras,
            name=f"{RUTA_PROTEGIDA}[{ESCENARIO}]",
            catch_response=True,
        ) as respuesta:
            # La latencia sale de un reloj monotónico; restar marcas de tiempo
            # de pared daría valores negativos si el reloj se ajusta a mitad de
            # la corrida.
            latencia_ms = (time.perf_counter() - inicio) * 1000
            ts_respuesta = time.time()

            if respuesta.request_meta.get("exception") is not None:
                fallo_transporte = type(respuesta.request_meta["exception"]).__name__
            else:
                status = respuesta.status_code
                error_code = _codigo_de_error(respuesta)

            observado = clasificar(status, error_code)

            # Un 403 sobre tráfico suplantado es el desenlace correcto, así que
            # la corrida se marca según coincida con la etiqueta de verdad y no
            # según el código HTTP. Sin esto, cada detección acertada aparecería
            # como una falla en el resumen de Locust.
            if _acierta(muestra.etiqueta_verdad, observado):
                respuesta.success()
            else:
                respuesta.failure(
                    f"esperado={muestra.etiqueta_verdad} observado={observado} status={status}"
                )

        if _registro is not None:
            _registro.anotar(Anotacion(
                request_id=request_id,
                ejecucion_id=EJECUCION_ID,
                escenario=ESCENARIO,
                modo=muestra.modo,
                etiqueta_verdad=muestra.etiqueta_verdad,
                session_id=muestra.session_id,
                device_alterado=muestra.device_alterado,
                distancia_km=muestra.distancia_km,
                ts_envio=ts_envio,
                ts_respuesta=ts_respuesta,
                latencia_ms=latencia_ms,
                status=status,
                error_code=error_code,
                veredicto_observado=observado,
                fallo_transporte=fallo_transporte,
                usuarios_configurados=USUARIOS_CONFIGURADOS,
            ))


def _serializar(contexto: dict) -> str:
    """Serializa el contexto de sesión tal como lo espera la cabecera."""
    import json

    return json.dumps(contexto)


def _codigo_de_error(respuesta) -> str | None:
    """Extrae `error_code` del cuerpo, cuando el Gateway devuelve un error."""
    if respuesta.status_code < 400:
        return None

    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None

    return cuerpo.get("error_code") if isinstance(cuerpo, dict) else None


def _acierta(etiqueta: str, observado: str) -> bool:
    """
    Indica si el desenlace observado coincide con la verdad-terreno.

    Solo `suspendida` y `enrutada` son decisiones de clasificación; un rechazo
    por formato o un fallo de dependencia no acierta ni se equivoca, y el
    análisis los excluye del denominador de la matriz.
    """
    from generador.poblacion import ETIQUETA_SUPLANTADA
    from generador.registro import OBSERVADO_ENRUTADA, OBSERVADO_SUSPENDIDA

    if etiqueta == ETIQUETA_SUPLANTADA:
        return observado == OBSERVADO_SUSPENDIDA

    return observado == OBSERVADO_ENRUTADA
