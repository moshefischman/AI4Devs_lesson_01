# Plan 001 — Motor central de análisis de código (AI Code Reviewer)

Status: done
Owner:
Last updated: 2026-11-14

Backlog: `ai code reviewer core` (`.plan/000-backlog.md`)
Depende de: —
Bloquea: `.plan/002-2026-11-14-test-generation-and-quality-report.md`

---

## Goal

Construir el motor que recibe código fuente (un archivo, un directorio o un diff de git),
lo envía a Claude mediante el SDK oficial de Anthropic y devuelve una **review**
estructurada: una lista de **findings** con severidad, categoría, ubicación
(`file:line`), explicación y sugerencia de corrección.

El motor es la base reutilizable sobre la que el plan 002 añade la generación de pruebas
unitarias y el reporte de calidad. Debe poder usarse como librería (import) y como CLI.

Resultado esperado al terminar:

```bash
python -m reviewer review path/to/file.py
python -m reviewer review src/ --format json --out .orchestrate/review/
git diff main | python -m reviewer review --diff -
```

## Scope

**Dentro del alcance**

- Paquete Python `reviewer/` en la raíz del repositorio (mismo lenguaje y SDK que los
  ejemplos existentes `10-messages.py`, `20-system-prompt.py`, `30-stream.py`).
- Recolección de entrada: archivo único, directorio (con filtro por extensión e
  ignorando `venv/`, `.git/`, `node_modules/`, archivos binarios) y diff unificado.
- Modelo de datos tipado (Pydantic) para `Review` y `Finding`.
- Cliente de Claude encapsulado en un único módulo (`reviewer/claude_client.py`).
- Prompt de sistema versionado para la revisión (`reviewer/prompt/review_system.md`).
- Salida estructurada garantizada por esquema (structured outputs), no parseo de texto libre.
- Troceado (chunking) de entradas grandes por archivo, sin truncar contenido en silencio.
- Formatos de salida: `text` (consola), `json` y `markdown`.
- CLI con `argparse`: subcomando `review`.
- Pruebas unitarias con `pytest` y cliente de Claude simulado (sin red).
- Actualización de `.doc/glossary.md`, `.doc/architecture.md` y
  `.doc/product-definition.md` con los términos y criterios de este plan.

**Fuera del alcance**

- Generación de pruebas unitarias y reporte de calidad → plan 002.
- Interfaz web, `frontend/` (Next.js) y cualquier árbol `backend/` (la tarea no lleva
  `stack:full`).
- Base de datos o persistencia más allá de archivos en `.orchestrate/`.
- Integración con GitHub/GitLab (comentarios en PR), CI y webhooks.
- Aplicar automáticamente las correcciones sugeridas al código revisado.

## Assumptions

1. El repositorio actual es un proyecto Python con el SDK `anthropic` y `python-dotenv`;
   la credencial se resuelve desde `.env` / entorno (`ANTHROPIC_API_KEY`) o un perfil de
   `ant auth login`. El motor **nunca** lee, imprime ni registra el valor de la clave.
2. Python 3.10+ (ya exigido por `README.md`).
3. Modelo por defecto: `claude-opus-5-5`, configurable por variable de entorno
   `REVIEWER_MODEL` y flag `--model` (ver Q2).
4. Thinking adaptativo (siempre activo en Opus 5.5) con `output_config.effort`
   configurable; por defecto `high` para revisión de código.
5. Se usa streaming (`client.messages.stream(...)` + `get_final_message()`) para
   peticiones con entrada o salida grande, evitando timeouts HTTP.
6. Las reglas de `.claude/rules/code-style.md` (sin `;`) aplican a JS/TS; en Python se
   sigue PEP 8 y el estilo de los scripts existentes.
7. Nombres de dominio en singular según `.claude/rules/naming.md`: `review`, `finding`,
   `severity`, `category`.
8. Los artefactos generados por la herramienta durante el desarrollo van a
   `.orchestrate/` (nunca a `docs/`).

## Open Questions

Resueltas el 2026-11-14: el usuario aprobó las respuestas recomendadas para todas las preguntas.

**Q1 — ¿Dónde vive el motor?** Las reglas del repo describen un producto `frontend/`
(Next.js) sin backend, pero el código real es Python con el SDK de Anthropic, y la clave
de API no debe exponerse en un navegador.
- A) Paquete Python `reviewer/` + CLI en la raíz. **(Recomendada)** — coherente con el
  código existente, mantiene la clave fuera del cliente y no crea `backend/`.
- B) Ruta de API en Next.js dentro de `frontend/` — requiere marcar la tarea `stack:full`
  y crear el frontend desde cero.
> Respuesta (2026-11-14): A — paquete Python `reviewer/` + CLI en la raíz.

**Q2 — ¿Modelo por defecto?** Los ejemplos usan `claude-haiku-4-5`.
- A) `claude-opus-5-5` con effort `high`. **(Recomendada)** — mejor calidad de revisión;
  configurable por entorno/flag.
- B) `claude-sonnet-5-5` — más barato para volumen alto.
- C) `claude-haiku-5-5` — el más barato, menor profundidad de análisis.
> Respuesta (2026-11-14): A — `claude-opus-5-5` con effort `high`.

**Q3 — ¿Habilitar fallback del servidor ante `stop_reason: "refusal"`?** Código de
seguridad (exploits, criptografía) puede activar clasificadores.
- A) Sí, `fallbacks: "default"` con la beta `server-side-fallback-2026-07-01`.
  **(Recomendada)**
- B) No; reportar el rechazo como error del archivo y continuar con el resto.
> Respuesta (2026-11-14): A — `fallbacks: "default"` con la beta `server-side-fallback-2026-07-01`.

**Q4 — ¿Taxonomía de severidad?**
- A) `critical` · `high` · `medium` · `low` · `info`. **(Recomendada)**
- B) `error` · `warning` · `suggestion`.
> Respuesta (2026-11-14): A — `critical` · `high` · `medium` · `low` · `info`.

**Q5 — ¿Categorías de finding?**
- A) `bug` · `security` · `performance` · `maintainability` · `style` · `test`.
  **(Recomendada)**
- B) Lista libre definida por el modelo.
> Respuesta (2026-11-14): A — `bug` · `security` · `performance` · `maintainability` · `style` · `test`.

**Q6 — ¿Tamaño máximo de entrada por ejecución?**
- A) Límite configurable (por defecto 200 archivos / 400K tokens estimados con
  `messages.count_tokens`); si se excede, abortar con mensaje claro antes de llamar a la
  API. **(Recomendada)**
- B) Sin límite.
> Respuesta (2026-11-14): A — límite configurable (200 archivos / 400K tokens), abortar antes de llamar a la API.

## Steps

1. **Rama y dependencias**
   - Crear la rama `feat/code-review-engine` (no trabajar en `main`).
   - Añadir `requirements.txt` (`anthropic`, `python-dotenv`, `pydantic`) y
     `requirements-dev.txt` (`pytest`, `pytest-cov`, `ruff`).
   - Añadir `pyproject.toml` con configuración de `pytest` y `ruff`.
   - Confirmar que `.env` y `venv/` están en `.gitignore`.

2. **Glosario y documentación de dominio** (antes de usar los términos en código)
   - `.doc/glossary.md`: `review`, `finding`, `severity`, `category`, `chunk`, `source`.
   - `.doc/product-definition.md`: propósito, alcance y criterios AC02–AC07 (ver
     Validation).
   - `.doc/architecture.md`: componentes y flujo de datos del motor.

3. **Modelo de datos** — `reviewer/model.py`
   - `Severity` y `Category` (enums según Q4/Q5).
   - `Finding`: `file`, `line_start`, `line_end`, `severity`, `category`, `title`,
     `explanation`, `suggestion` (opcional, fragmento de código).
   - `Review`: `findings`, `summary`, `model`, `usage` (tokens de entrada/salida/caché),
     `source_count`, `created_at`.
   - Validación: `line_start >= 1`, `line_end >= line_start`, `file` relativo al repo.

4. **Recolección de entrada** — `reviewer/source.py`
   - `collect_file(path)`, `collect_dir(path, include, exclude)`, `parse_diff(text)`.
   - Ignorar binarios, `venv/`, `.git/`, `node_modules/`, `.orchestrate/`, `.env*`.
   - Numerar las líneas al construir el contenido enviado (para que `line` sea fiable).
   - Nunca incluir archivos `.env` ni que coincidan con patrones de secretos.

5. **Troceado** — `reviewer/chunk.py`
   - Agrupar archivos en chunks bajo el presupuesto de tokens (`count_tokens`).
   - Un archivo que por sí solo excede el presupuesto se divide por bloques de líneas
     con solapamiento, conservando la numeración original.
   - Si se supera el límite total (Q6), error explícito: nunca truncar en silencio.

6. **Cliente de Claude** — `reviewer/claude_client.py`
   - `anthropic.Anthropic()` sin clave en código.
   - Prompt de sistema estable en `reviewer/prompt/review_system.md`, enviado con
     `cache_control` para aprovechar la caché entre chunks.
   - Salida estructurada con `output_config.format` / `client.messages.parse()` usando
     el esquema de `Review`.
   - Streaming para peticiones grandes; `max_tokens` holgado (p. ej. 32000).
   - Manejo de `stop_reason`: `max_tokens` → reintentar dividiendo el chunk;
     `refusal` → según Q3.
   - Errores tipados de más a menos específico (`AuthenticationError`,
     `RateLimitError`, `APIStatusError`, `APIConnectionError`); los reintentos 429/5xx
     los hace el SDK (`max_retries`). Aplicar el skill `error-handling`.
   - Registrar `_request_id` y `usage` en modo `--verbose`; nunca registrar la clave.

7. **Servicio de revisión** — `reviewer/review_service.py`
   - `review(sources, options) -> Review`: chunk → llamada por chunk → fusión.
   - Deduplicar findings (mismo `file` + rango + `title`) y ordenar por severidad.
   - Resumen global generado a partir de los findings fusionados.

8. **Salida** — `reviewer/render.py`
   - `text` con colores ANSI opcionales, `json` (serialización de `Review`) y
     `markdown` (tabla de findings agrupada por archivo).
   - `--out <dir>` escribe `review-<timestamp>.{json,md}`; por defecto
     `.orchestrate/review/`.

9. **CLI** — `reviewer/__main__.py` y `reviewer/cli.py`
   - `python -m reviewer review <path|-> [--diff] [--format] [--out] [--model]
     [--effort] [--min-severity] [--include] [--exclude] [--verbose]`.
   - Código de salida: `0` sin findings ≥ `--fail-on` (por defecto `high`), `1` si los
     hay, `2` error de uso/configuración, `3` error de la API.

10. **Pruebas** (aplicar el skill `writing-tests`) — `tests/unit/`
    - `test_model.py`, `test_source.py`, `test_chunk.py`, `test_review_service.py`,
      `test_render.py`, `test_cli.py`.
    - Cliente de Claude simulado mediante inyección de dependencias (sin red).
    - Camino feliz y camino de fallo para cada módulo (respuesta inválida, refusal,
      `max_tokens`, error de autenticación, entrada vacía, límite excedido).
    - Fixtures mínimas en `tests/fixtures/` (sin secretos reales).

11. **Prueba de humo opcional contra la API real** — `tests/smoke/test_live_review.py`,
    marcada `@pytest.mark.live` y excluida por defecto; se ejecuta solo con aprobación
    porque consume crédito.

12. **README** — sección "AI Code Reviewer": instalación, uso de la CLI y ejemplo de salida.

## Validation

Checklist para QA; cada punto se prueba con un comando o una prueba.

- [ ] AC01 (adaptado a Python) — `ruff check .` limpio y `pytest -m "not live"` en verde.
- [ ] AC02 — `pytest tests/unit/test_model.py`: `Finding` rechaza `line_start < 1`,
      `line_end < line_start` y rutas absolutas.
- [ ] AC03 — `pytest tests/unit/test_source.py`: se ignoran `venv/`, `.git/`, binarios
      y cualquier `.env*`; un diff unificado produce las líneas cambiadas correctas.
- [ ] AC04 — `pytest tests/unit/test_chunk.py`: ningún chunk excede el presupuesto y la
      suma de líneas enviadas cubre el 100 % de la entrada (sin truncado).
- [ ] AC05 — `pytest tests/unit/test_review_service.py`: con el cliente simulado, los
      findings se fusionan, deduplican y ordenan por severidad; `refusal` y
      `max_tokens` se manejan según Q3/Step 6.
- [ ] AC06 — `pytest tests/unit/test_cli.py`: códigos de salida `0/1/2/3` según el
      caso; `--format json` produce JSON válido contra el esquema de `Review`.
- [ ] AC07 — `pytest tests/unit/test_claude_client.py`: la clave nunca aparece en logs
      ni en la salida (`caplog` + `capsys`), incluso con `--verbose`.
- [ ] `pytest --cov=reviewer --cov-fail-under=85 -m "not live"` pasa.
- [ ] `python -m reviewer review tests/fixtures/buggy_sample.py --format json` (con
      aprobación, contra la API real) devuelve al menos un finding `bug` en la línea
      sembrada.
- [ ] `.doc/glossary.md` contiene `review`, `finding`, `severity`, `category`.

## Risks

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Números de línea alucinados | Findings no accionables | Enviar el código con líneas numeradas; validar que `line` exista en el archivo y descartar/marcar los que no |
| Coste por entradas grandes | Gasto inesperado | `count_tokens` previo, límite de Q6, caché del prompt de sistema, `--model` / `--effort` configurables |
| Falsos positivos | Pérdida de confianza | `--min-severity`, prompt que exige evidencia concreta, deduplicación |
| Rechazos (`refusal`) en código de seguridad | Archivos sin revisar | Fallback de servidor (Q3) y reporte explícito por archivo |
| Fuga de secretos al enviar código | Exposición de credenciales | Excluir `.env*` y patrones de secretos en `source.py`; prueba AC03 |
| Deriva de la API del SDK | Roturas tras actualizar | Fijar versión en `requirements.txt`; aislar el SDK en `claude_client.py` |
| Conflicto con las reglas "frontend-only" | Rechazo en revisión | Resolver Q1 antes de ejecutar |

## Rollout Order

1. Steps 1–2 (rama, dependencias, documentación de dominio).
2. Steps 3–5 (modelo, entrada, troceado) con sus pruebas — sin dependencia de red.
3. Step 6–7 (cliente y servicio) con el cliente simulado.
4. Steps 8–9 (salida y CLI).
5. Step 10 completo + cobertura; Step 11 solo con aprobación.
6. Step 12 (README) → revisión → aprobación explícita para commit/merge.
7. Al terminar, el plan 002 puede pasar a `active`.

## Rollback

- Todo el trabajo está aislado en `feat/code-review-engine` y en el paquete nuevo
  `reviewer/`; los scripts existentes (`10-messages.py`, `20-system-prompt.py`,
  `30-stream.py`) no se modifican.
- Antes del merge: descartar la rama.
- Después del merge: revertir el commit de merge (`git revert -m 1 <merge-sha>`) con
  aprobación explícita; eliminar `.orchestrate/review/` (generado, no versionado).
- Los cambios en `.doc/` van en un commit propio para poder revertirlos por separado.
