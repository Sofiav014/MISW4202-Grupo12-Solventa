#!/usr/bin/env python3
"""Orquesta una corrida controlada de Locust para un escenario (A-G).

La misma infraestructura (journey/mock/redis) se reutiliza entre escenarios;
solo el adaptador se recrea, para limpiar el Circuit Breaker.

Procedimiento por repetición:
  1. reset:   recrea el contenedor del adaptador (breaker limpio, ESCENARIO/
              EJECUCION_ID correctos en sus logs), vacía la caché y deja el
              mock en `normal` — un estado neutro, igual sin importar qué
              escenario corrió antes.
  2. warm-up: corrida corta y descartable contra ese estado neutro (cliente
              dedicado, no el del escenario), para estabilizar el proceso
              antes de medir (JIT/pools de conexión/primer request de Flask).
  3. prepara: recién ahora se establecen las condiciones iniciales propias
              del escenario (mock degradado, breaker forzado, cache miss) —
              después del warm-up, para que sea la corrida medida —y no el
              warm-up descartado— la que capture sus transiciones.
  4. medición: corrida de Locust headless con los parámetros de carga; el
              resultado queda en resultados/locust/escenario_<X>/<id>_stats.csv

Requiere: servicios levantados con `docker-compose up -d` (excepto el propio
adaptador, que este script recrea) y Docker en el PATH.

Corre uno, varios o los siete escenarios en una sola invocación: `--escenario`
acepta una letra, una lista separada por comas, o `TODOS`. El procedimiento
de arriba (reset -> warm-up -> prepara -> mide) se repite completo para cada
combinación de escenario x repetición, así que cada uno queda con su breaker
limpio y su propio estado inicial sin importar qué corrió antes.

Ejemplos:
  python run_escenario.py --escenario A --repeticiones 3 --ejecucion-id run1
  python run_escenario.py --escenario B -u 20 -r 5 -t 90s
  python run_escenario.py --escenario G -u 100 -r 20 -t 2m --modo-proveedor lento
  python run_escenario.py --escenario F --cliente-ids 99999
  python run_escenario.py --escenario A,B,C --repeticiones 3 --ejecucion-id suite1
  python run_escenario.py --escenario TODOS --repeticiones 3 --ejecucion-id suite1
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import control
from escenarios import CLIENTE_WARMUP, ESCENARIOS

RAIZ = Path(__file__).resolve().parent
LOCUSTFILE = RAIZ / "locustfile.py"

_PATRON_DURACION = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$")


def segundos_desde_duracion(cadena: str) -> int:
    """Convierte '90s'/'2m'/'1h30m'/'90' a segundos, para el manifest."""
    coincidencia = _PATRON_DURACION.fullmatch(cadena.strip())
    if not coincidencia or not any(coincidencia.groups()):
        raise ValueError(f"duración inválida: {cadena!r}")
    h, m, s = (int(g) if g else 0 for g in coincidencia.groups())
    return h * 3600 + m * 60 + s


def parse_args():
    """Define y parsea los argumentos de línea de comandos del orquestador."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--escenario", "-e", required=True,
        help="Letra (A-G), lista separada por comas (A,B,C) o TODOS para correr los siete",
    )
    p.add_argument(
        "--usuarios", "-u", type=int, default=None,
        help="Override de usuarios concurrentes (se aplica a cada escenario seleccionado)",
    )
    p.add_argument(
        "--spawn-rate", "-r", type=float, default=None,
        help="Override de usuarios/segundo al iniciar (se aplica a cada escenario seleccionado)",
    )
    p.add_argument(
        "--duracion", "-t", default=None,
        help="Override de duración medida, ej. 60s/2m (se aplica a cada escenario seleccionado)",
    )
    p.add_argument("--repeticiones", type=int, default=1, help="Repeticiones por escenario, para reproducibilidad")
    p.add_argument("--ejecucion-id", default="local", help="Prefijo de EJECUCION_ID y de los CSV")
    p.add_argument(
        "--modo-proveedor", default=None,
        help="Override del modo del mock (aplica en D/F/G; si corrés varios escenarios se usa en todos los que lo soportan)",
    )
    p.add_argument("--cliente-ids", default=None, help="Lista separada por comas; override del pool de cada escenario seleccionado")
    p.add_argument("--journey-url", default=control.JOURNEY_URL)
    p.add_argument("--warmup-duracion", default="10s", help="Duración del warm-up descartable")
    p.add_argument("--warmup-usuarios", type=int, default=2)
    p.add_argument("--sin-warmup", action="store_true", help="Omite el warm-up")
    p.add_argument(
        "--sin-reinicio-adaptador",
        action="store_true",
        help="No recrea el contenedor del adaptador entre corridas (el breaker no queda limpio)",
    )
    p.add_argument(
        "--continuar-si-falla",
        action="store_true",
        help="Si un escenario/repetición falla (error del harness, no fallas HTTP esperadas), "
        "sigue con el resto de la suite en vez de abortar",
    )
    p.add_argument("--resultados-dir", default=str(RAIZ.parent / "resultados"))
    return p.parse_args()


def resolver_escenarios(valor: str) -> list:
    """Traduce '--escenario' (letra, lista o TODOS) a una lista de letras válidas."""
    if valor.strip().upper() == "TODOS":
        return sorted(ESCENARIOS)
    letras = [l.strip().upper() for l in valor.split(",") if l.strip()]
    invalidas = [l for l in letras if l not in ESCENARIOS]
    if invalidas or not letras:
        raise SystemExit(
            f"Escenario(s) inválido(s): {', '.join(invalidas) or valor!r} — "
            f"válidos: {', '.join(sorted(ESCENARIOS))} o TODOS"
        )
    return letras


def ejecutar_locust(*, host, duracion, usuarios, spawn_rate, escenario, cliente_ids, csv_prefix):
    """Corre Locust en modo headless con los parámetros dados y espera a que termine.

    Si `csv_prefix` es None, la corrida es descartable (solo resumen en
    consola); si no, escribe los CSV de resultados con ese prefijo.
    """
    env = os.environ.copy()
    env["ESCENARIO"] = escenario
    env["CLIENTE_IDS"] = ",".join(cliente_ids)
    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(LOCUSTFILE),
        "--headless",
        "--host", host,
        "-u", str(usuarios),
        "-r", str(spawn_rate),
        "-t", duracion,
        # Los escenarios C/D/F esperan fallas (circuito abierto, cache miss);
        # eso no es un error del harness, así que no debe tumbar el proceso.
        "--exit-code-on-error", "0",
    ]
    if csv_prefix is not None:
        Path(csv_prefix).parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--csv", csv_prefix, "--csv-full-history"]
    else:
        cmd += ["--only-summary"]
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env, cwd=RAIZ)


def verificar_integridad_minima(csv_prefix: str) -> None:
    """Chequeo mínimo antes de pasar a análisis: el CSV existe y tiene tráfico."""
    ruta = Path(f"{csv_prefix}_stats.csv")
    if not ruta.exists():
        raise RuntimeError(f"no se generó {ruta.name}")
    with ruta.open() as f:
        filas = {fila["Name"]: fila for fila in csv.DictReader(f)}
    agregada = filas.get("Aggregated")
    if agregada is None:
        raise RuntimeError(f"{ruta.name} no tiene fila 'Aggregated'")
    if int(agregada["Request Count"]) == 0:
        raise RuntimeError(f"{ruta.name} quedó con 0 requests — la corrida no generó tráfico")


def correr_repeticion(esc, args, indice):
    """Ejecuta una repetición completa de un escenario: reset, warm-up,
    preparación de condiciones, manifest y la corrida de Locust medida."""
    id_corrida = f"{args.ejecucion_id}_rep{indice}"
    print(f"\n=== Escenario {esc.letra} ({esc.nombre}) — corrida {id_corrida} ===")
    print(f"    {esc.notas}")

    print(
        "-> reseteando infraestructura (breaker, Redis, mock -> normal)"
        + (" [sin recrear adaptador]" if args.sin_reinicio_adaptador else "") + "..."
    )
    control.resetear_infraestructura(
        escenario=esc.letra, ejecucion_id=id_corrida, reiniciar=not args.sin_reinicio_adaptador
    )

    if not args.sin_warmup:
        # Contra estado neutro y cliente dedicado, antes de preparar el escenario.
        print(f"-> warm-up ({args.warmup_usuarios} usuarios, {args.warmup_duracion}, descartado)...")
        ejecutar_locust(
            host=args.journey_url,
            duracion=args.warmup_duracion,
            usuarios=args.warmup_usuarios,
            spawn_rate=args.warmup_usuarios,
            escenario=f"{esc.letra}-warmup",
            cliente_ids=[CLIENTE_WARMUP],
            csv_prefix=None,
        )

    print(f"-> preparando condiciones iniciales del escenario {esc.letra}...")
    esc.preparar(esc)

    directorio_corrida = Path(args.resultados_dir) / f"escenario_{esc.letra}" / id_corrida
    ruta_manifest = control.guardar_manifest_corrida(
        escenario=esc.letra,
        corrida_id=id_corrida,
        modo_mock=esc.modo_proveedor,
        usuarios=esc.usuarios,
        duration_seconds=segundos_desde_duracion(esc.duracion),
        spawn_rate=esc.spawn_rate,
        resultados_dir=Path(args.resultados_dir),
    )
    print(f"-> manifest: {ruta_manifest or '(ya existía, no se sobrescribió)'}")

    hilo_secuencia = None
    if esc.secuencia_especial is not None:
        hilo_secuencia = threading.Thread(target=esc.secuencia_especial, args=(esc,), daemon=True)
        hilo_secuencia.start()

    csv_prefix = str(directorio_corrida / "results")
    ejecutar_locust(
        host=args.journey_url,
        duracion=esc.duracion,
        usuarios=esc.usuarios,
        spawn_rate=esc.spawn_rate,
        escenario=esc.letra,
        cliente_ids=esc.cliente_ids,
        csv_prefix=csv_prefix,
    )

    if hilo_secuencia is not None:
        hilo_secuencia.join(timeout=5)

    verificar_integridad_minima(csv_prefix)
    print(f"-> resultados: {csv_prefix}_stats.csv")


def _aplicar_overrides(esc, args) -> None:
    """Sobrescribe los valores por defecto de un escenario con los flags de CLI presentes."""
    if args.usuarios is not None:
        esc.usuarios = args.usuarios
    if args.spawn_rate is not None:
        esc.spawn_rate = args.spawn_rate
    if args.duracion is not None:
        esc.duracion = args.duracion
    if args.modo_proveedor is not None:
        esc.modo_proveedor = args.modo_proveedor
    if args.cliente_ids is not None:
        esc.cliente_ids = [c.strip() for c in args.cliente_ids.split(",") if c.strip()]


def main():
    """Corre todas las combinaciones escenario x repetición pedidas y resume el resultado."""
    args = parse_args()
    letras = resolver_escenarios(args.escenario)

    print(f"Suite: {len(letras)} escenario(s) x {args.repeticiones} repetición(es) = "
          f"{len(letras) * args.repeticiones} corridas -> {', '.join(letras)}")

    corridas_ok = []
    corridas_falladas = []
    for letra in letras:
        esc = ESCENARIOS[letra]
        _aplicar_overrides(esc, args)
        for i in range(1, args.repeticiones + 1):
            try:
                correr_repeticion(esc, args, i)
            except Exception as error:
                corridas_falladas.append((letra, i, str(error)))
                print(f"!! Escenario {letra} rep {i} falló: {error}", file=sys.stderr)
                if not args.continuar_si_falla:
                    raise
            else:
                corridas_ok.append((letra, i))

    if len(letras) * args.repeticiones > 1:
        print(f"\n=== Suite terminada: {len(corridas_ok)} ok, {len(corridas_falladas)} fallidas ===")
        if corridas_falladas:
            for letra, i, error in corridas_falladas:
                print(f"  - {letra} rep{i}: {error}")
        print(f"Resultados en: {args.resultados_dir}")

    if corridas_falladas:
        sys.exit(1)


if __name__ == "__main__":
    main()
