# Plan 002 — Generación automática de pruebas unitarias y reporte de calidad

Status: done
Owner:
Last updated: 2026-11-14

Backlog: `unit test generation and quality report` (`.plan/000-backlog.md`)
Depende de: `.plan/001-2026-11-14-code-review-engine.md` (debe estar `done`)

---

## Goal

Extender el motor del plan 001 con dos capacidades:

1. **Generación de pruebas unitarias**: a partir de un archivo o módulo, Claude propone
   un archivo de pruebas ejecutable (camino feliz, casos límite y caminos de fallo). La
   herramienta **ejecuta** las pruebas generadas en un proceso aislado, descarta o
   repara las que no compilan o fallan por errores del propio test, y entrega solo
   pruebas que pasan (o que fallan señalando un bug real, marcado como tal).
2. **Reporte de calidad**: un informe consolidado (Markdown + JSON) que combina la
   review del plan 001, el resultado de las pruebas, la cobertura medida y métricas
   estáticas, con una puntuación de calidad y un veredicto `pass` / `warn` / `fail`.

Resultado esperado:

```bash
python -m reviewer test-gen path/to/module.py --out .orchestrate/test-gen/
python -m reviewer report src/ --out .orchestrate/quality-report/
```

## Scope

**Dentro del alcance**

- Subcomando `test-gen`: genera pruebas `pytest` para código Python.
- Bucle generar → ejecutar → reparar con un número máximo de iteraciones.
- Ejecución aislada de pruebas generadas (subproceso con timeout, directorio temporal,
  sin red y sin acceso a `.env`).
- Medición de cobertura con `pytest-cov` / `coverage.py` antes y después.
- Subcomando `report`: orquesta review (plan 001) + test-gen opcional + cobertura +
  métricas estáticas (`ruff`, complejidad ciclomática con `radon`).
- Modelo de datos `TestSuite`, `TestCase`, `QualityReport`, `QualityScore`.
- Puntuación de calidad con pesos configurables y umbrales de veredicto.
- Salida `markdown` y `json`, escrita en `.orchestrate/`.
- Pruebas unitarias del propio módulo con cliente de Claude simulado.

**Fuera del alcance**

- Lenguajes distintos de Python para las pruebas generadas (ver Q1).
- Pruebas de integración o e2e generadas.
- Escribir las pruebas generadas directamente en `tests/` del proyecto analizado sin
  confirmación (`--apply` explícito, ver Q4).
- Dashboard web, gráficos interactivos, `frontend/` o `backend/`.
- Histórico de reportes en base de datos (solo archivos).
- Publicar el reporte en servicios externos (PR, Slack, etc.).

## Assumptions

1. El plan 001 está terminado: existen `reviewer/model.py`, `reviewer/source.py`,
   `reviewer/chunk.py`, `reviewer/claude_client.py`, `reviewer/review_service.py` y la CLI.
2. Se reutiliza `claude_client.py` (modelo, effort, streaming, structured outputs,
   manejo de errores y de `refusal`) sin duplicar la integración con el SDK.
3. El código analizado es importable desde la raíz del proyecto objetivo (o se indica
   con `--root`).
4. Las pruebas generadas siguen el skill `writing-tests`: comportamiento y no
   implementación, deterministas, aisladas, una intención por prueba, sin secretos,
   tiempo y aleatoriedad congelados cuando aplique.
5. Ejecutar código generado por un modelo es un riesgo: siempre en subproceso con
   timeout, en directorio temporal y con un entorno sin variables de credenciales.
6. Nombres en singular (`.claude/rules/naming.md`): `test_case`, `test_suite`,
   `report`, `score`.

## Open Questions

Resueltas el 2026-11-14: el usuario aprobó las respuestas recomendadas para todas las preguntas.

**Q1 — ¿Lenguajes soportados para generar pruebas en la primera versión?**
- A) Solo Python (`pytest`). **(Recomendada)** — se puede ejecutar y verificar de
  extremo a extremo con las herramientas ya presentes.
- B) Python + TypeScript (`vitest`) — duplica el ejecutor y el cálculo de cobertura.
> Respuesta (2026-11-14): A — solo Python (`pytest`).

**Q2 — ¿Iteraciones máximas del bucle generar → ejecutar → reparar?**
- A) 3 iteraciones por archivo. **(Recomendada)** — equilibrio coste/calidad.
- B) 1 (sin reparación).
- C) Configurable sin límite.
> Respuesta (2026-11-14): A — 3 iteraciones por archivo.

**Q3 — ¿Qué hacer con una prueba generada que falla de forma consistente tras las
iteraciones?**
- A) Si Claude la clasifica como "bug real en el código", conservarla marcada con
  `@pytest.mark.xfail(reason="posible bug: …", strict=True)` y añadir un finding
  `bug`; en caso contrario, descartarla. **(Recomendada)**
- B) Descartarla siempre.
> Respuesta (2026-11-14): A — `xfail(strict=True)` + finding `bug` si es bug real; si no, descartar.

**Q4 — ¿Dónde se escriben las pruebas generadas?**
- A) En `.orchestrate/test-gen/<timestamp>/`; `--apply` las copia a `tests/unit/`
  del proyecto objetivo, sin sobrescribir archivos existentes. **(Recomendada)**
- B) Directamente en `tests/unit/`.
> Respuesta (2026-11-14): A — `.orchestrate/test-gen/<timestamp>/`; `--apply` copia sin sobrescribir.

**Q5 — ¿Pesos de la puntuación de calidad (0–100)?**
- A) Findings de la review 40 % · cobertura 30 % · pruebas que pasan 15 % · lint y
  complejidad 15 %. Veredicto: `pass` ≥ 80, `warn` 60–79, `fail` < 60 o cualquier
  finding `critical`. **(Recomendada)**
- B) Definir otros pesos/umbrales.
> Respuesta (2026-11-14): A — 40 / 30 / 15 / 15; `pass` ≥ 80, `warn` 60–79, `fail` < 60 o cualquier `critical`.

**Q6 — ¿Aislamiento para ejecutar pruebas generadas?**
- A) Subproceso `python -m pytest` con timeout (60 s), directorio temporal, entorno
  limpio sin `ANTHROPIC_*` ni otras credenciales. **(Recomendada)** — sin dependencias
  nuevas.
- B) Contenedor Docker — mayor aislamiento, requiere Docker instalado.
> Respuesta (2026-11-14): A — subproceso con timeout de 60 s, directorio temporal y entorno sin credenciales.

## Steps

1. **Rama y dependencias**
   - Crear la rama `feat/test-generation` desde `main` con el plan 001 ya fusionado.
   - Añadir `pytest-cov` y `radon` a `requirements.txt` (necesarios en tiempo de
     ejecución de la herramienta).

2. **Glosario y documentación**
   - `.doc/glossary.md`: `test_case`, `test_suite`, `quality report`, `quality score`,
     `verdict`.
   - `.doc/product-definition.md`: criterios AC08–AC14 (ver Validation).
   - `.doc/architecture.md`: flujo `test-gen` y `report`.

3. **Modelo de datos** — ampliar `reviewer/model.py`
   - `TestCase`: `name`, `intent` (happy/edge/failure), `target` (función/clase),
     `status` (passed/failed/xfail/discarded), `failure_reason`.
   - `TestSuite`: `target_file`, `test_file`, `code`, `cases`, `iteration_count`,
     `coverage_before`, `coverage_after`.
   - `QualityScore`: componentes ponderados + total.
   - `QualityReport`: `review`, `test_suites`, `coverage`, `lint`, `complexity`,
     `score`, `verdict`, `created_at`.

4. **Análisis del objetivo** — `reviewer/test_target.py`
   - Extraer con `ast` funciones y clases públicas, firmas, docstrings e imports.
   - Detectar dependencias externas (red, disco, tiempo, aleatoriedad) que las pruebas
     deban simular.
   - Incluir pruebas existentes del módulo como contexto para no duplicarlas.

5. **Generador** — `reviewer/test_gen_service.py`
   - Prompt de sistema en `reviewer/prompt/test_gen_system.md` con las reglas del skill
     `writing-tests` y convenciones de `pytest`.
   - Salida estructurada: `TestSuite` (código + lista de casos con intención).
   - Exigir al menos un caso feliz y un caso de fallo por función pública.

6. **Ejecutor aislado** — `reviewer/test_runner.py`
   - Escribir la suite en un directorio temporal y ejecutar
     `python -m pytest --json-report`/`-rA` + `--cov` en un subproceso con timeout.
   - Entorno sin variables `ANTHROPIC_*` ni otras credenciales; `cwd` controlado.
   - Parsear el resultado por caso y la cobertura de líneas/ramas del archivo objetivo.

7. **Bucle de reparación** — en `test_gen_service.py`
   - Reenviar a Claude los errores de colección/ejecución (traceback recortado a lo
     relevante, sin truncar el código) hasta Q2 iteraciones.
   - Clasificar los fallos persistentes según Q3.
   - Rechazar pruebas que no aportan cobertura nueva o que dependen del orden.

8. **Métricas estáticas** — `reviewer/metric.py`
   - `ruff check --output-format json` y `radon cc -j` sobre el objetivo.
   - Normalizar a puntuaciones 0–100.

9. **Reporte de calidad** — `reviewer/report_service.py` y `reviewer/render.py`
   - Orquestar: review (plan 001) → test-gen (opcional, `--with-test-gen`) →
     cobertura → métricas → puntuación (Q5) → veredicto.
   - Markdown: resumen ejecutivo, veredicto, puntuación por componente, top findings,
     tabla de cobertura por archivo, pruebas generadas y su estado, recomendaciones.
   - JSON: serialización completa de `QualityReport`.
   - Escritura en `.orchestrate/quality-report/report-<timestamp>.{md,json}`.

10. **CLI** — ampliar `reviewer/cli.py`
    - `test-gen <path> [--out] [--apply] [--max-iteration] [--model] [--effort]`.
    - `report <path> [--with-test-gen] [--out] [--fail-on-verdict warn|fail]`.
    - Códigos de salida: `0` pass, `1` veredicto ≥ `--fail-on-verdict`, `2` uso,
      `3` API, `4` fallo del ejecutor de pruebas.

11. **Pruebas del propio módulo** (skill `writing-tests`) — `tests/unit/`
    - `test_test_target.py`, `test_test_gen_service.py`, `test_test_runner.py`,
      `test_metric.py`, `test_report_service.py`, `test_cli_report.py`.
    - Cliente de Claude simulado devolviendo suites válidas, inválidas y con fallos.
    - El ejecutor se prueba con suites de fixture reales (pasan, fallan, cuelgan → timeout).
    - Prueba explícita de que el subproceso no recibe variables de credenciales.

12. **Prueba de humo opcional** — `tests/smoke/test_live_test_gen.py`
    (`@pytest.mark.live`), solo con aprobación porque consume crédito.

13. **README** — secciones "Generación de pruebas" y "Reporte de calidad" con ejemplos.

## Validation

Checklist para QA; cada punto se prueba con un comando o una prueba.

- [ ] AC01 (adaptado a Python) — `ruff check .` limpio y `pytest -m "not live"` en verde.
- [ ] AC08 — `pytest tests/unit/test_test_target.py`: se extraen todas las funciones y
      clases públicas de `tests/fixtures/sample_module.py`.
- [ ] AC09 — `pytest tests/unit/test_test_runner.py`: una suite que cuelga se aborta
      por timeout y se reporta como `discarded`; el entorno del subproceso no contiene
      ninguna variable `ANTHROPIC_*`.
- [ ] AC10 — `pytest tests/unit/test_test_gen_service.py`: el bucle se detiene en el
      máximo de Q2; un fallo persistente clasificado como bug queda como `xfail` y
      genera un finding `bug` (Q3).
- [ ] AC11 — `pytest tests/unit/test_report_service.py`: con entradas fijas, la
      puntuación y el veredicto coinciden con los pesos/umbrales de Q5; un finding
      `critical` fuerza `fail`.
- [ ] AC12 — `pytest tests/unit/test_cli_report.py`: `report --format json` produce JSON
      válido contra el esquema de `QualityReport`; códigos de salida `0/1/2/3/4`.
- [ ] AC13 — `test-gen` sin `--apply` no escribe nada fuera de `.orchestrate/`;
      con `--apply` no sobrescribe archivos existentes.
- [ ] AC14 — Con aprobación (API real): `python -m reviewer test-gen
      tests/fixtures/sample_module.py` produce una suite que pasa con
      `python -m pytest <suite>` y eleva la cobertura del módulo respecto a la base.
- [ ] `pytest --cov=reviewer --cov-fail-under=85 -m "not live"` pasa.

## Risks

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Ejecutar código generado (efectos secundarios, borrado de archivos, red) | Daño local, fuga de datos | Subproceso aislado, directorio temporal, timeout, entorno sin credenciales (Q6) |
| Pruebas que pasan pero no verifican nada (asserts triviales) | Cobertura engañosa | Exigir intención por caso, rechazar casos sin aporte de cobertura, revisión en el prompt |
| Pruebas que "fijan" un bug como comportamiento esperado | Falsa seguridad | Cruzar con findings de la review; política de Q3 |
| Pruebas inestables (tiempo, aleatoriedad, orden) | Ruido en CI | Ejecutar cada suite 2 veces y en orden aleatorio; descartar las no deterministas |
| Coste del bucle de reparación | Gasto elevado | Límite de iteraciones (Q2), caché del prompt de sistema, `--effort` configurable |
| Puntuación percibida como arbitraria | Baja adopción | Pesos configurables y desglose por componente en el reporte |

## Rollout Order

1. Plan 001 en `done` y fusionado.
2. Steps 1–3 (rama, dependencias, documentación, modelo).
3. Steps 4 y 6 (análisis del objetivo y ejecutor aislado) — sin red, totalmente testeables.
4. Steps 5 y 7 (generador y bucle de reparación) con cliente simulado.
5. Steps 8–9 (métricas y reporte).
6. Step 10 (CLI) y Step 11 (pruebas + cobertura); Step 12 solo con aprobación.
7. Step 13 (README) → revisión → aprobación explícita para commit/merge.

## Rollback

- Trabajo aislado en `feat/test-generation`; los módulos nuevos no alteran el
  comportamiento del subcomando `review` del plan 001.
- Antes del merge: descartar la rama.
- Después del merge: `git revert -m 1 <merge-sha>` con aprobación explícita; el motor de
  review sigue funcionando porque solo se añadieron módulos y subcomandos.
- Eliminar `.orchestrate/test-gen/` y `.orchestrate/quality-report/` (generados, no
  versionados). Pruebas copiadas con `--apply` se identifican por la cabecera
  `# generated by reviewer test-gen` para poder retirarlas.
