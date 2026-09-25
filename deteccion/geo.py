from math import asin, cos, radians, sin, sqrt


# Radio medio terrestre. Basta para decidir si dos puntos distan más o menos
# de unas decenas de kilómetros, que es la precisión que exige la regla.
RADIO_TIERRA_KM = 6371.0088


def distancia_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """
    Calcula la distancia sobre la superficie terrestre entre dos coordenadas.

    Se usa la fórmula de Haversine porque la alternativa ingenua —tratar
    latitud y longitud como un plano— subestima la distancia a medida que
    crece la latitud, y el experimento compara contra un radio fijo en
    kilómetros.

    Args:
        lat_a, lon_a: Coordenadas del primer punto, en grados decimales.
        lat_b, lon_b: Coordenadas del segundo punto, en grados decimales.

    Returns:
        Distancia entre ambos puntos, en kilómetros.
    """
    lat_a_rad, lat_b_rad = radians(lat_a), radians(lat_b)
    delta_lat = radians(lat_b - lat_a)
    delta_lon = radians(lon_b - lon_a)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat_a_rad) * cos(lat_b_rad) * sin(delta_lon / 2) ** 2
    )

    return 2 * RADIO_TIERRA_KM * asin(sqrt(a))
