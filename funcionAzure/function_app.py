import azure.functions as func
import json
import tempfile
import os
import shutil
import zipfile
import requests
from azure.data.tables import TableServiceClient
from azure.storage.blob import BlobServiceClient, ContentSettings

# Definición de tests para cada archivo por nombre
def test_sumar(f):
    return f(2, 3) == 5 and f(-1, 1) == 0 and f(100, 200) == 300

def test_es_primo(f):
    return f(2) and f(3) and not f(4) and f(13) and not f(1)

def test_factorial(f):
    return f(0) == 1 and f(1) == 1 and f(3) == 6 and f(5) == 120

def test_es_palindromo(f):
    return f("oso") and f("radar") and not f("hola") and f("reconocer") and not f("python")

def test_contar_vocales(f):
    return f("hola") == 2 and f("murcielago") == 5 and f("xyz") == 0 and f("AEIOU") == 5

TESTS = {
    "ejercicio1.py": ("sumar", test_sumar),
    "ejercicio2.py": ("es_primo", test_es_primo),
    "ejercicio3.py": ("factorial", test_factorial),
    "ejercicio4.py": ("es_palindromo", test_es_palindromo),
    "ejercicio5.py": ("contar_vocales", test_contar_vocales),
}

# Funcion para guardad datos en Azure Table Storage

def guardar_en_tabla(resultado):
    try:
        connection_string = os.getenv("AZURE_TABLE_CONN")
        table_name = "notas"

        if not connection_string:
            return "No se encontró la conexión a la tabla."

        service = TableServiceClient.from_connection_string(conn_str=connection_string)
        table_client = service.get_table_client(table_name=table_name)

        entidad = {
            "PartitionKey": "Resultados",
            "RowKey": resultado["nombre"],
            "NotaTotal": resultado["nota_total"],
            "Intentos": resultado.get("intentos", 1),
            "Errores": json.dumps(resultado["errores"]),
            "PorEjercicio": json.dumps(resultado["nota_por_ejercicio"])
        }

        table_client.upsert_entity(mode="merge", entity=entidad)
        return None
    except Exception as e:
        return f"Error al guardar en la tabla: {e}"

# Función para generar y subir el HTML al contenedor $web
def generar_y_subir_html():
    try:
        connection_string = os.getenv("AZURE_TABLE_CONN")

        # Obtener datos de la tabla
        service = TableServiceClient.from_connection_string(connection_string)
        table_client = service.get_table_client("notas")
        entidades = list(table_client.query_entities("PartitionKey eq 'Resultados'"))

        # Generar HTML con todos los campos relevantes
        filas = ""
        for entidad in entidades:
            nombre = entidad.get("RowKey", "")
            nota = entidad.get("NotaTotal", 0)
            intentos = entidad.get("Intentos", 1)
            errores = json.loads(entidad.get("Errores", "[]"))
            errores_str = "<ul>" + "".join(f"<li>{e}</li>" for e in errores) + "</ul>" if errores else "Sin errores"
            por_ejercicio = json.loads(entidad.get("PorEjercicio", "{}"))
            detalles = "<ul>" + "".join(f"<li>{k}: {v}</li>" for k, v in por_ejercicio.items()) + "</ul>"

            filas += f"""
            <tr>
                <td>{nombre}</td>
                <td>{nota}</td>
                <td>{intentos}</td>
                <td>{errores_str}</td>
                <td>{detalles}</td>
            </tr>
            """

        html = f"""
        <html>
        <head>
            <title>Notas de alumnos</title>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 90%; margin: auto; }}
                th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; vertical-align: top; }}
                th {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
        <h2 style="text-align:center;">Tabla de Resultados</h2>
        <table>
            <tr>
                <th>Alumno</th>
                <th>Nota Total</th>
                <th>Intentos</th>
                <th>Errores</th>
                <th>Notas por ejercicio</th>
            </tr>
            {filas}
        </table>
        </body>
        </html>
        """

        # Subir HTML al contenedor $web
        blob_service = BlobServiceClient.from_connection_string(connection_string)
        container = blob_service.get_container_client("$web")
        container.upload_blob("index.html", html.encode('utf-8'),
                              overwrite=True,
                              content_settings=ContentSettings(content_type='text/html'))

        return None
    except Exception as e:
        return f"Error al generar o subir el HTML: {e}"


app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="test_practicas")
def test_practicas(req: func.HttpRequest) -> func.HttpResponse:

    try:
        body = req.get_json()
        alumno = body.get("alumno")
        practica = body.get("practica")
        intentos = body.get("intentos")

        if not alumno or not practica:
            return func.HttpResponse("Faltan parámetros 'alumno' o 'practica'", status_code=400)

        # Construir nombre y URL del archivo
        zip_name = f"{alumno}_{practica}_ejercicios.zip"

        # URL completa del blob + token SAS
        base_blob_url = os.getenv("BLOB_URL")
        if not base_blob_url:
            return func.HttpResponse("No se ha configurado la URL del blob", status_code=500)
        sas_token = os.getenv("SAS_TOKEN") 
        if not sas_token:
            return func.HttpResponse("No se ha configurado el token SAS", status_code=500)
        blob_url = f"{base_blob_url}/{zip_name}?{sas_token}"

        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, zip_name)

        # Descargar el archivo del blob
        try:
            r = requests.get(blob_url)
            if r.status_code != 200:
                return func.HttpResponse(f"No se pudo descargar el ZIP: {r.status_code}", status_code=400)
            with open(zip_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            return func.HttpResponse(f"Error al descargar el ZIP: {e}", status_code=500)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)

            practica_path = os.path.join(temp_dir, practica)
            if not os.path.isdir(practica_path):
                return func.HttpResponse(f"No se encontró la carpeta '{practica}'", status_code=400)

            nota_por_ejercicio = {}
            errores = []
            total_puntos = 0

            for archivo, (nombre_funcion, test_funcion) in TESTS.items():
                path = os.path.join(practica_path, archivo)

                if not os.path.isfile(path):
                    nota_por_ejercicio[archivo] = 0
                    errores.append(f"{archivo}: No entregado")
                    continue

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        codigo = f.read()
                except Exception as e:
                    errores.append(f"{archivo}: Error al leer archivo - {e}")
                    nota_por_ejercicio[archivo] = 0
                    continue

                local_vars = {}
                try:
                    exec(codigo, {}, local_vars)
                except Exception as e:
                    errores.append(f"{archivo}: Error de ejecución - {e}")
                    nota_por_ejercicio[archivo] = 0
                    continue

                if nombre_funcion not in local_vars:
                    errores.append(f"{archivo}: funcion '{nombre_funcion}' no encontrada")
                    nota_por_ejercicio[archivo] = 0
                    continue

                try:
                    passed = test_funcion(local_vars[nombre_funcion])
                    if passed:
                        nota_por_ejercicio[archivo] = 1
                        total_puntos += 1
                    else:
                        nota_por_ejercicio[archivo] = 0
                        errores.append(f"{archivo}: Test fallido")
                except Exception as e:
                    errores.append(f"{archivo}: Error al ejecutar test - {e}")
                    nota_por_ejercicio[archivo] = 0

            resultado = {
                "nombre": alumno,
                "intentos": intentos,
                "nota_total": total_puntos,
                "nota_por_ejercicio": nota_por_ejercicio,
                "errores": errores if errores else None
            }
            
            # Guardar resultado en la tabla
            error_guardado = guardar_en_tabla(resultado)
            if error_guardado:
                resultado["error_guardado_tabla"] = error_guardado
            error_html = generar_y_subir_html()
            if error_html:
                resultado["error_subida_html"] = error_html

            

            return func.HttpResponse(
                json.dumps(resultado, ensure_ascii=False),
                mimetype="application/json",
                status_code=200
            )

        finally:
            shutil.rmtree(temp_dir)

    except Exception as e:
        return func.HttpResponse(f"Error general: {e}", status_code=500) 
