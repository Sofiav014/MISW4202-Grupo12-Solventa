"""Análisis de recuperación del escenario E (6.3).

Lee evidencia de 5.4 sin modificarla y devuelve tablas, figuras y conclusiones
para el notebook. No ejecuta corridas ni produce resultados al importar.
"""

from __future__ import annotations
import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

STATE_MAP_E63 = {"OPEN": "OPEN", "HALF_OPEN": "HALF_OPEN", "CLOSED": "CLOSED"}
SOURCE_MAP_E63 = {"CACHE": "CACHE", "PROVIDER": "PROVIDER", "OPEN_FINANCE": "PROVIDER", "NONE": "NONE"}
REQUEST_MAP_E63 = {"ejecucion_id": "corrida_id", "ts_wall": "timestamp"}
TIME_COLUMNS_E63 = ["tiempo_open_a_half_open", "tiempo_half_open_a_closed", "tiempo_recuperacion_total"]

@dataclass
class RecoveryInput:
    """Borde de entrada compartido por paquetes reales y fixtures en memoria."""
    requests: list[dict[str, Any]]
    log: list[dict[str, Any]]
    manifests: list[dict[str, Any]]
    label: str
    quality: list[dict[str, str]] = field(default_factory=list)

def add_issue(issues: list[dict[str, str]], run: str, message: str) -> None:
    """Acumula incidencias para mostrarlas juntas, sin descartar evidencia."""
    issues.append({"corrida_id": run, "incidencia": message})

def check_hash(content: bytes, expected: str, context: str) -> str:
    """Comprueba SHA-256; acepta solo la equivalencia CRLF→LF documentada."""
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"{context}: SHA-256 ausente o inválido")
    if hashlib.sha256(content).hexdigest() == expected:
        return "exacto"
    if hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() == expected:
        return "equivalencia CRLF→LF; integridad estricta del checkout NO confirmada"
    raise ValueError(f"{context}: discrepancia SHA-256 no explicada por CRLF→LF")

def csv_value(value: Any) -> str:
    """Representación de celda documentada por 5.4 para cotejar CSV y log."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

def load_results(results_dir: Path, log_path: Path) -> RecoveryInput:
    """Lee paquetes E sin escribir, verifica hashes y coteja todas sus filas con el log."""
    folders = sorted(p for p in (results_dir / "escenario_E").glob("*") if p.is_dir())
    if not folders:
        raise FileNotFoundError("No hay paquetes de escenario E; no se sustituyen por fixtures.")
    content = log_path.read_bytes()
    log = []
    for number, line in enumerate(content.splitlines(), 1):
        if line.strip():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{log_path.name}, línea {number}: JSON inválido") from error
            if not isinstance(event, dict):
                raise ValueError(f"{log_path.name}, línea {number}: se requiere un objeto")
            log.append(event)
    requests, manifests, quality = [], [], []
    for folder in folders:
        report = json.loads((folder / "integridad.json").read_bytes())
        if report.get("estado") != "valido" or report.get("version") != 1:
            raise ValueError(f"{folder.name}: constancia de integridad inválida")
        inventory = report.get("archivos", {})
        required = {"manifest.json", "results.csv", "procedencia.json"}
        allowed = required | {"results_stats.csv", "results_stats_history.csv", "results_failures.csv", "results_exceptions.csv"}
        if not required <= set(inventory) <= allowed:
            raise ValueError(f"{folder.name}: inventario incompleto o no reconocido")
        for name, expected in inventory.items():
            status = check_hash((folder / name).read_bytes(), expected, f"{folder.name}/{name}")
            if status != "exacto":
                add_issue(quality, folder.name, f"{name}: {status}")
        manifest = json.loads((folder / "manifest.json").read_bytes())
        identity = (manifest.get("escenario"), manifest.get("corrida_id"))
        if identity != ("E", folder.name) or identity != (report.get("escenario"), report.get("corrida_id")):
            raise ValueError(f"{folder.name}: identidad incompatible con manifest/integridad")
        origin = json.loads((folder / "procedencia.json").read_bytes())
        if origin.get("archivo_fuente") != log_path.name:
            raise ValueError(f"{folder.name}: archivo fuente distinto del log configurado")
        status = check_hash(content, origin.get("sha256_fuente"), f"{folder.name}/log fuente")
        if status != "exacto":
            add_issue(quality, folder.name, f"Log fuente: {status}")
        if origin.get("ventana_medicion_confirmada") is not True:
            add_issue(quality, folder.name, "Ventana de medición no confirmada: " + " ".join(origin.get("limitaciones", [])))
        reader = csv.DictReader(io.StringIO((folder / "results.csv").read_text(encoding="utf-8-sig"), newline=""), strict=True)
        rows = list(reader)
        columns = reader.fieldnames or []
        if not columns or len(columns) != len(set(columns)) or any(None in r or None in r.values() for r in rows):
            raise ValueError(f"{folder.name}: cabecera duplicada o ancho de CSV inválido")
        if not rows or len(rows) != report.get("cantidad_registros"):
            raise ValueError(f"{folder.name}: conteo de peticiones vacío o distinto de integridad")
        if any((r.get("escenario"), r.get("ejecucion_id")) != identity for r in rows):
            raise ValueError(f"{folder.name}: peticiones de otra identidad")
        source = [r for r in log if r.get("event_type") == "request" and (r.get("escenario"), r.get("ejecucion_id")) == identity]
        if len(source) != len(rows) or any(
            any(row[c] != csv_value(event.get(c)) for c in columns)
            for row, event in zip(rows, source)
        ):
            raise ValueError(f"{folder.name}: results.csv y el snapshot del log no corresponden")
        requests.extend(rows)
        manifests.append(manifest)
    logging.getLogger("analisis.6.3").info("Cargadas %d corridas E y %d peticiones reales", len(manifests), len(requests))
    return RecoveryInput(requests, log, manifests, "REALES", quality)

def normalize_state(value: Any) -> Any:
    """Normaliza únicamente estados reconocidos; un estado ausente sigue ausente."""
    return STATE_MAP_E63.get(str(value).strip().upper().replace("-", "_"), pd.NA)

def parse_bool(value: Any) -> Any:
    """Interpreta booleanos reales/CSV sin convertir la cadena 'false' en True."""
    if value is None or pd.isna(value) or value == "":
        return pd.NA
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    raise ValueError(f"proveedor_invocado inválido: {value!r}")

def parse_utc(series: pd.Series, context: str) -> pd.Series:
    """Exige timestamps con zona horaria y devuelve UTC, sin adivinar epoch."""
    values = []
    for value in series:
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{context}: timestamp no parseable {value!r}") from error
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError(f"{context}: timestamp ausente o sin zona horaria")
        values.append(stamp.tz_convert("UTC"))
    return pd.Series(values, index=series.index, dtype="datetime64[ns, UTC]")

def normalize_requests(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Mapea en un solo lugar el contrato por petición al modelo interno de 6.3."""
    frame = pd.DataFrame(records).rename(columns=REQUEST_MAP_E63)
    required = {"request_id", "corrida_id", "escenario", "timestamp", "fuente_respuesta"}
    if frame.empty or not required <= set(frame):
        raise ValueError(f"Peticiones vacías o faltan campos: {sorted(required - set(frame))}")
    for column in ("estado_circuito_inicio", "estado_circuito_fin"):
        frame[column] = frame.get(column, pd.Series(index=frame.index, dtype="object")).map(normalize_state)
    frame["timestamp"] = parse_utc(frame["timestamp"], "peticiones")
    for column in ("timestamp_inicio", "timestamp_fin"):
        frame[column] = pd.to_numeric(frame.get(column, pd.Series(index=frame.index, dtype=float)), errors="coerce")
    elapsed = frame["timestamp_fin"] - frame["timestamp_inicio"]
    if (elapsed.dropna() < 0).any() or np.isinf(frame[["timestamp_inicio", "timestamp_fin"]]).any().any():
        raise ValueError("Reloj monotónico inválido: fin anterior al inicio o infinito")
    frame["inicio_utc_aproximado"] = frame["timestamp"] - pd.to_timedelta(elapsed, unit="s")
    frame["fuente_respuesta"] = frame["fuente_respuesta"].map(SOURCE_MAP_E63).fillna("DESCONOCIDA")
    frame["proveedor_invocado"] = frame.get("proveedor_invocado", pd.Series(index=frame.index, dtype="object")).map(parse_bool).astype("boolean")
    frame["resultado"] = frame.get("resultado", pd.Series(index=frame.index, dtype="object"))
    frame["estado_circuito"] = frame["estado_circuito_fin"].combine_first(frame["estado_circuito_inicio"])
    return frame.sort_values(["escenario", "corrida_id", "timestamp"], kind="stable").reset_index(drop=True)

def associate_transitions(log: list[dict[str, Any]], issues: list[dict[str, str]]) -> pd.DataFrame:
    """Asocia eventos por identidad/request_id o por ventana única; nunca adivina."""
    all_requests = normalize_requests([r for r in log if r.get("event_type") == "request"])
    windows = []
    for identity, group in all_requests.groupby(["escenario", "corrida_id"]):
        windows.append((identity, group["inicio_utc_aproximado"].fillna(group["timestamp"]).min(), group["timestamp"].max()))
    request_ids = all_requests.groupby("request_id").apply(lambda g: set(zip(g.escenario, g.corrida_id)), include_groups=False).to_dict()
    events = []
    for line, event in enumerate(log, 1):
        if event.get("logger") != "adaptador.circuit_breaker" or not {"estado_anterior", "estado_nuevo"} <= set(event):
            continue
        timestamp = parse_utc(pd.Series([event.get("ts_wall")]), f"transición línea {line}").iloc[0]
        candidates = [key for key, start, end in windows if start <= timestamp <= end]
        direct = (event.get("escenario"), event.get("ejecucion_id", event.get("corrida_id")))
        by_request = request_ids.get(event.get("request_id"), set())
        if all(direct):
            identity, method = direct, "identidad explícita"
            if by_request and by_request != {identity}:
                for key in by_request | {identity}:
                    if key[0] == "E":
                        add_issue(issues, key[1], f"Transición {timestamp}: asociación ambigua por identidades contradictorias; excluida")
                continue
        elif len(by_request) == 1:
            identity, method = next(iter(by_request)), "request_id"
        elif len(by_request) > 1 or len(candidates) != 1:
            for key in set(candidates) | by_request:
                if key[0] == "E":
                    add_issue(issues, key[1], f"Transición {timestamp}: asociación ambigua; excluida")
            continue
        else:
            identity, method = candidates[0], "ventana observada única (asociación temporal)"
        if identity[0] != "E":
            continue
        before, after = normalize_state(event["estado_anterior"]), normalize_state(event["estado_nuevo"])
        if pd.isna(before) or pd.isna(after):
            add_issue(issues, identity[1], f"Transición {timestamp}: estado desconocido; excluida")
            continue
        events.append({"escenario": "E", "corrida_id": identity[1], "timestamp": timestamp,
                       "estado_anterior": before, "estado_circuito": after, "asociacion": method})
    columns = ["escenario", "corrida_id", "timestamp", "estado_anterior", "estado_circuito", "asociacion"]
    return pd.DataFrame(events, columns=columns).sort_values(["corrida_id", "timestamp"], kind="stable").reset_index(drop=True)

def validate_results(requests: pd.DataFrame, conditions: pd.DataFrame, issues: list[dict[str, str]]) -> None:
    """Valida trazabilidad y capacidad analítica; conserva corridas incompletas."""
    if requests.empty or set(requests.escenario) != {"E"}:
        raise ValueError("El dataset analítico debe contener exclusivamente E y no estar vacío")
    for column in ("corrida_id", "request_id"):
        if requests[column].isna().any() or requests[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Identificador obligatorio vacío: {column}")
    if requests.duplicated(["corrida_id", "request_id"]).any():
        raise ValueError("request_id duplicado dentro de corrida: posible reutilización de identidad")
    if conditions.corrida_id.duplicated().any() or set(conditions.corrida_id) != set(requests.corrida_id):
        raise ValueError("Manifests y peticiones no tienen las mismas identidades únicas")
    for run, group in requests.groupby("corrida_id"):
        if group.estado_circuito.isna().all():
            raise ValueError(f"{run}: imposible determinar estados del Circuit Breaker")
        if not group.fuente_respuesta.isin(["CACHE", "PROVIDER"]).any():
            add_issue(issues, run, "Sin fuentes CACHE/PROVIDER observadas; retorno no determinable")
        for label, mask in {
            "fuente desconocida": group.fuente_respuesta.eq("DESCONOCIDA"),
            "estado final/inicial incompleto": group.estado_circuito_fin.isna() | group.estado_circuito_inicio.isna(),
            "sin inicio UTC aproximado": group.inicio_utc_aproximado.isna(),
            "PROVIDER con proveedor_invocado=False (indicador derivado del estado inicial)": group.fuente_respuesta.eq("PROVIDER") & group.proveedor_invocado.eq(False).fillna(False),
        }.items():
            if mask.any():
                add_issue(issues, run, f"{int(mask.sum())} peticiones: {label}")

def normalize_results(raw: RecoveryInput) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, str]]]:
    """Construye tablas internas sin modificar el input ni consultar configuración viva."""
    issues = list(raw.quality)
    requests = normalize_requests(raw.requests)
    conditions = []
    for manifest in raw.manifests:
        if manifest.get("escenario") != "E":
            raise ValueError("Manifest ajeno al escenario E")
        timeout = manifest.get("circuit_breaker", {}).get("reset_timeout_seconds")
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not np.isfinite(timeout) or timeout <= 0):
            raise ValueError("reset_timeout_seconds inválido")
        if timeout is None:
            add_issue(issues, manifest["corrida_id"], "Manifest sin reset_timeout; delta no determinable")
        conditions.append({"corrida_id": manifest["corrida_id"], "reset_timeout": timeout,
                           "fail_max": manifest.get("circuit_breaker", {}).get("fail_max"),
                           "ttl": manifest.get("cache", {}).get("ttl_seconds"),
                           "modo_mock": manifest.get("mock_openfinance", {}).get("modo"),
                           "usuarios": manifest.get("carga", {}).get("usuarios"),
                           "duracion": manifest.get("carga", {}).get("duration_seconds")})
    conditions = pd.DataFrame(conditions)
    validate_results(requests, conditions, issues)
    transitions = associate_transitions(raw.log, issues)
    ambiguous_runs = {issue["corrida_id"] for issue in issues if "asociación ambigua" in issue["incidencia"]}
    requests["asociacion_ambigua"] = requests.corrida_id.isin(ambiguous_runs)
    return requests, transitions, conditions, issues

def first_timestamp(frame: pd.DataFrame) -> pd.Timestamp:
    """Primer evento por orden temporal, o NaT cuando no existe evidencia."""
    return frame.timestamp.min() if not frame.empty else pd.NaT

def elapsed_seconds(start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Diferencia en segundos; extremos ausentes permanecen no determinables."""
    if pd.isna(start) or pd.isna(end):
        return float("nan")
    if end < start:
        raise ValueError("Secuencia temporal inválida: intervalo negativo")
    return (end - start).total_seconds()

def extract_recovery_metrics(requests: pd.DataFrame, transitions: pd.DataFrame,
                             conditions: pd.DataFrame) -> pd.DataFrame:
    """Extrae un episodio principal por corrida y cuenta reintentos/reaperturas."""
    rows = []
    for run in sorted(requests.corrida_id.unique()):
        events = transitions[transitions.corrida_id.eq(run)]
        opened = first_timestamp(events[events.estado_circuito.eq("OPEN") & events.estado_anterior.isin(["CLOSED", "HALF_OPEN"])])
        after_open = events[events.timestamp.ge(opened)] if pd.notna(opened) else events.iloc[:0]
        half = first_timestamp(after_open[after_open.estado_anterior.eq("OPEN") & after_open.estado_circuito.eq("HALF_OPEN")])
        closed = first_timestamp(after_open[after_open.estado_anterior.eq("HALF_OPEN") & after_open.estado_circuito.eq("CLOSED")])
        if pd.notna(half) and pd.notna(closed) and half > closed:
            half = pd.NaT  # no usar HALF_OPEN de un episodio posterior para completar uno incompleto
        timeout = conditions.loc[conditions.corrida_id.eq(run), "reset_timeout"].iloc[0]
        timeout = float(timeout) if pd.notna(timeout) else float("nan")
        open_half = elapsed_seconds(opened, half)
        episode = after_open[after_open.timestamp.le(closed)] if pd.notna(closed) else after_open
        rows.append({"corrida_id": run, "t_open": opened, "t_half_open": half, "t_closed": closed,
                     "tiempo_open_a_half_open": open_half,
                     "tiempo_half_open_a_closed": elapsed_seconds(half, closed),
                     "tiempo_recuperacion_total": elapsed_seconds(opened, closed),
                     "reset_timeout": timeout, "delta_reset_timeout": open_half - timeout,
                     "intentos_half_open": int(episode.estado_circuito.eq("HALF_OPEN").sum()),
                     "reaperturas_antes_cierre": int((episode.estado_anterior.eq("HALF_OPEN") & episode.estado_circuito.eq("OPEN")).sum()),
                     "reaperturas_despues_cierre": int((events.timestamp.gt(closed) & events.estado_circuito.eq("OPEN")).sum()) if pd.notna(closed) else 0})
    return pd.DataFrame(rows)

def verify_provider_recovery(group: pd.DataFrame, metric: pd.Series) -> dict[str, Any]:
    """Devuelve cinco evidencias, conteos posteriores y veredicto conservador."""
    opened, half, closed = metric.t_open, metric.t_half_open, metric.t_closed
    sequence = all(pd.notna(t) for t in (opened, half, closed))
    cache = group[group.timestamp.ge(opened) & group.timestamp.lt(half)
                  & group.estado_circuito_inicio.eq("OPEN") & group.estado_circuito_fin.eq("OPEN")
                  & group.fuente_respuesta.eq("CACHE")] if sequence else group.iloc[:0]
    probes = group[group.inicio_utc_aproximado.le(closed) & group.timestamp.ge(closed)
                   & group.timestamp.ge(half) & group.estado_circuito_inicio.isin(["OPEN", "HALF_OPEN"])
                   & group.estado_circuito_fin.eq("CLOSED") & group.fuente_respuesta.eq("PROVIDER")
                   & group.resultado.eq("exitoso")] if sequence else group.iloc[:0]
    post = group.iloc[:0]
    unclassified = 0
    probe_id = None
    if not probes.empty:
        probe = probes.sort_values("timestamp").iloc[0]
        probe_id = probe.request_id
        if pd.notna(probe.timestamp_fin):
            post = group[group.timestamp_inicio.gt(probe.timestamp_fin) & group.timestamp.gt(probe.timestamp)]
            unclassified = int((group.timestamp.gt(probe.timestamp) & group.timestamp_inicio.isna()).sum())
    successful_provider = post.fuente_respuesta.eq("PROVIDER") & post.resultado.eq("exitoso")
    closed_provider = successful_provider & post.estado_circuito_inicio.eq("CLOSED") & post.estado_circuito_fin.eq("CLOSED")
    contrary = (post.fuente_respuesta.isin(["CACHE", "NONE"]) | post.resultado.eq("fallido")).any() or metric.reaperturas_despues_cierre > 0
    ambiguous = bool(group.get("asociacion_ambigua", pd.Series(False, index=group.index)).any())
    complete_post = not post.empty and bool(closed_provider.fillna(False).all()) and unclassified == 0 and not ambiguous
    evidence = [not cache.empty, pd.notna(half), not probes.empty, pd.notna(closed), bool(closed_provider.any())]
    if contrary:
        verdict, reason = "No", "Caché/NONE/fallo o reapertura después del cierre observado"
    elif all(evidence) and complete_post:
        verdict, reason = "Sí", "Cinco evidencias presentes; todas las peticiones posteriores observadas son exitosas del proveedor"
    else:
        missing = [name for name, ok in zip(["caché en OPEN", "HALF_OPEN", "prueba del proveedor", "CLOSED", "proveedor posterior"], evidence) if not ok]
        if not complete_post:
            missing.append("observación posterior completa y consistente")
        if ambiguous:
            missing.append("asociación inequívoca de todas las transiciones candidatas")
        verdict, reason = "No determinable", "Falta: " + "; ".join(missing)
    return {"corrida_id": metric.corrida_id,
            "cache_durante_open": evidence[0], "half_open_observado": evidence[1],
            "prueba_proveedor": evidence[2], "closed_observado": evidence[3], "proveedor_despues_closed": evidence[4],
            "request_id_prueba": probe_id, "cache_en_open": len(cache), "peticiones_posteriores": len(post),
            "provider_posterior": int(post.fuente_respuesta.eq("PROVIDER").sum()),
            "cache_posterior": int(post.fuente_respuesta.eq("CACHE").sum()),
            "none_posterior": int(post.fuente_respuesta.eq("NONE").sum()),
            "fuente_desconocida_posterior": int(post.fuente_respuesta.eq("DESCONOCIDA").sum()),
            "peticiones_sin_inicio_para_clasificar": unclassified,
            "observacion_posterior_s": elapsed_seconds(closed, post.timestamp.max()) if not post.empty else float("nan"),
            "retorno_open_finance_confirmado": verdict, "motivo": reason}

def build_summary(requests: pd.DataFrame, transitions: pd.DataFrame,
                  conditions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Construye comparación, evidencia y estadística descriptiva sin inferencia."""
    metrics = extract_recovery_metrics(requests, transitions, conditions)
    evidence = pd.DataFrame([verify_provider_recovery(requests[requests.corrida_id.eq(row.corrida_id)], row)
                             for _, row in metrics.iterrows()])
    summary = metrics.merge(evidence[["corrida_id", "retorno_open_finance_confirmado"]], on="corrida_id", validate="one_to_one")
    statistics = summary[TIME_COLUMNS_E63].agg(["count", "mean", "min", "max", "std"]).T
    statistics.columns = ["valores_validos", "promedio_s", "minimo_s", "maximo_s", "desviacion_estandar_s"]
    statistics.insert(0, "corridas_totales", len(summary))
    statistics["valores_validos"] = statistics.valores_validos.astype(int)
    statistics.index.name = "intervalo"
    return summary, evidence, statistics

def plot_recovery(requests: pd.DataFrame, transitions: pd.DataFrame,
                  summary: pd.DataFrame, label: str) -> list[plt.Figure]:
    """Crea las tres figuras de 6.3; no escribe artefactos externos."""
    colors = {"CACHE": "#D97706", "PROVIDER": "#047857", "NONE": "#DC2626", "DESCONOCIDA": "#6B7280"}
    state_levels = {"OPEN": 0, "HALF_OPEN": 1, "CLOSED": 2}
    figures = []
    fig, axes = plt.subplots(len(summary), 1, figsize=(12, 3.3 * len(summary)), squeeze=False, layout="constrained")
    for ax, (_, row) in zip(axes[:, 0], summary.iterrows()):
        group = requests[requests.corrida_id.eq(row.corrida_id)]
        events = transitions[transitions.corrida_id.eq(row.corrida_id)]
        if pd.isna(row.t_open):
            ax.text(0.5, 0.5, "Apertura no determinable", ha="center", transform=ax.transAxes)
            continue
        x = (events.timestamp - row.t_open).dt.total_seconds()
        y = events.estado_circuito.map(state_levels)
        ax.step(x, y, where="post", color="#2563EB", linewidth=2)
        ax.scatter(x, y, color="#2563EB", zorder=3)
        if not events.empty:
            end = (group.timestamp.max() - row.t_open).total_seconds()
            ax.hlines(y.iloc[-1], x.iloc[-1], end, colors="#2563EB", linestyles="dotted", label="Sin otra transición registrada")
        ax.set(title=row.corrida_id, xlabel="Segundos desde OPEN", yticks=[0, 1, 2], yticklabels=list(state_levels), ylim=(-0.3, 2.3))
        ax.grid(alpha=0.2)
        if pd.notna(row.t_half_open) and pd.notna(row.t_closed):
            half = (row.t_half_open - row.t_open).total_seconds()
            close = (row.t_closed - row.t_open).total_seconds()
            inset = ax.inset_axes([0.52, 0.13, 0.43, 0.50])
            inset.step(x, y, where="post", color="#2563EB", marker="o")
            inset.set(xlim=(half - 0.12, close + 0.12), ylim=(-0.2, 2.2), yticks=[0, 1, 2], yticklabels=["OPEN", "HALF_OPEN", "CLOSED"])
            inset.set_title(f"Detalle: prueba/cierre {row.tiempo_half_open_a_closed:.3f} s", fontsize=9)
            inset.tick_params(labelsize=8)
            inset.grid(alpha=0.2)
    fig.suptitle(f"6.3 · Estado del Circuit Breaker — DATOS: {label}")
    figures.append(fig)

    fig, axes = plt.subplots(len(summary), 1, figsize=(12, 2.7 * len(summary)), squeeze=False, layout="constrained")
    for ax, (_, row) in zip(axes[:, 0], summary.iterrows()):
        group = requests[requests.corrida_id.eq(row.corrida_id)]
        origin = row.t_open if pd.notna(row.t_open) else group.timestamp.min()
        for level, source in enumerate(colors):
            selected = group[group.fuente_respuesta.eq(source)]
            ax.scatter((selected.timestamp - origin).dt.total_seconds(), np.full(len(selected), level),
                       s=10, alpha=0.6, color=colors[source], label=source)
        for name, stamp, color in [("OPEN", row.t_open, "#DC2626"), ("HALF_OPEN", row.t_half_open, "#7C3AED"), ("CLOSED", row.t_closed, "#2563EB")]:
            if pd.notna(stamp):
                ax.axvline((stamp - origin).total_seconds(), color=color, linestyle="--", linewidth=1, label=name)
        ax.set(title=row.corrida_id, xlabel="Segundos desde OPEN (negativos: preparación)", yticks=range(4), yticklabels=list(colors), ylim=(-0.5, 3.5))
        ax.grid(alpha=0.2)
    axes[0, 0].legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8)
    fig.suptitle(f"6.3 · Fuente de cada respuesta — DATOS: {label}")
    figures.append(fig)

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2), layout="constrained")
    for ax, column, title in zip(axes, TIME_COLUMNS_E63, ["OPEN → HALF_OPEN", "HALF_OPEN → CLOSED", "Recuperación total"]):
        values = summary[column]
        bars = ax.bar(summary.corrida_id, values, color="#2563EB", width=0.55)
        ax.bar_label(bars, labels=[f"{v:.3f}" if pd.notna(v) else "N/D" for v in values], padding=3, fontsize=9)
        if values.notna().any():
            ax.set_ylim(0, max(float(values.max()) * 1.2, 0.001))
        ax.set(title=title, ylabel="Segundos")
        ax.tick_params(axis="x", rotation=15)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle(f"6.3 · Comparación por corrida (escalas independientes) — DATOS: {label}")
    figures.append(fig)
    return figures

def recovery_conclusions(summary: pd.DataFrame, evidence: pd.DataFrame,
                         statistics: pd.DataFrame, issues: list[dict[str, str]], label: str) -> str:
    """Redacta conclusiones derivadas de resultados, incluidos faltantes y procedencia."""
    total = statistics.loc["tiempo_recuperacion_total"]
    confirmed = int(summary.retorno_open_finance_confirmado.eq("Sí").sum())
    lines = [f"**DATOS: {label}.** Se analizaron **{len(summary)} corridas** del escenario E; "
             f"{int(total.valores_validos)} tienen recuperación total medible."]
    if total.valores_validos:
        lines.append(f"La recuperación total media fue **{total.promedio_s:.6f} s**, "
                     f"con rango **{total.minimo_s:.6f}–{total.maximo_s:.6f} s**. "
                     + (f"La desviación estándar muestral fue **{total.desviacion_estandar_s:.6f} s**."
                        if pd.notna(total.desviacion_estandar_s) else "La desviación estándar no es determinable con una sola corrida válida."))
    for column, name in zip(TIME_COLUMNS_E63[:2], ["OPEN→HALF_OPEN", "HALF_OPEN→CLOSED"]):
        values = summary[column].dropna()
        lines.append(f"{name}: promedio **{values.mean():.6f} s** en {len(values)} corridas válidas." if len(values) else f"{name}: no determinable.")
    deltas = summary.delta_reset_timeout.dropna()
    lines.append(f"Se pudo contrastar el timeout del manifest en **{len(deltas)} corridas**. "
                 + (f"El delta OPEN→HALF_OPEN − reset_timeout va de **{deltas.min():.6f} a {deltas.max():.6f} s**."
                    if len(deltas) else "No se inventó un reset_timeout para las entradas sin ese dato."))
    lines.append(f"Retorno a Open Finance confirmado en **{confirmed}/{len(summary)} corridas**, "
                 "limitado al intervalo observado y a la asociación de eventos documentada.")
    for _, row in evidence.iterrows():
        lines.append(f"- **{row.corrida_id}: {row.retorno_open_finance_confirmado}**. "
                     f"Caché durante OPEN: {row.cache_en_open}; posteriores: {row.provider_posterior} PROVIDER, "
                     f"{row.cache_posterior} CACHE, {row.none_posterior} NONE y {row.fuente_desconocida_posterior} de fuente desconocida. "
                     f"Observación posterior: {row.observacion_posterior_s:.3f} s. {row.motivo}.")
    incomplete = int(summary[["t_open", "t_half_open", "t_closed"]].isna().any(axis=1).sum())
    lines.append(f"**Anomalías y límites:** {incomplete} corridas con secuencia incompleta; "
                 f"{int(summary.reaperturas_antes_cierre.sum())} reaperturas antes del cierre y "
                 f"{int(summary.reaperturas_despues_cierre.sum())} después. "
                 f"La tabla de calidad conserva {len(issues)} incidencias de entrada. "
                 "Incluye, cuando aparecen, equivalencia CRLF en lugar de integridad estricta, ventana de medición "
                 "no certificada e inconsistencias de proveedor_invocado. Los eventos se asocian temporalmente cuando "
                 "no tienen identificadores. No se certifica una única llamada concurrente de prueba ni se calcula "
                 "tiempo desde la reparación efectiva del mock. La baja variabilidad descriptiva no prueba generalización.")
    if label != "REALES":
        lines.append("> Los valores mostrados validan únicamente el pipeline analítico de 6.3 y no constituyen resultados experimentales del sistema.")
    return "\n\n".join(lines)
