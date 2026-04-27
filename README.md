# Blackboard Notify — Bot de Alertas a Discord

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-354%20passing-28a745.svg)](#-tests)

Bot automatizado que monitorea las tareas pendientes de Blackboard Ultra y envía notificaciones a Discord.

---

## 📋 Descripción

**Blackboard Notify** es un bot de Python que se ejecuta automáticamente cada 3 horas, inicia sesión en tu cuenta de SENATI Blackboard (vía Microsoft O365 / SAML), extrae todas las tareas con fechas de vencimiento del calendario y notifica a un canal de Discord con recordatorios en español: nuevas tareas, cambios de fecha, resumen semanal, y alertas de proximidad.

---

## ✨ Características

- 🔐 **Login automático con Microsoft O365/SAML** — sin intervención manual
- 📚 **Scraping completo** — extrae todas las tareas con fechas de entrega del calendario de vencimiento
- 💾 **Base de datos SQLite** — seguimiento histórico de tareas y notificaciones
- 🆕 **Detección de tareas nuevas** — notifica cuando un profesor publica una tarea
- 📅 **Detección de cambio de fecha** — alerta cuando se modifica la fecha de entrega
- 📋 **Resumen semanal** — los lunes a las 00:00, lista de todas las tareas de la semana
- 🔴 **Alerta 24 horas** — recordatorio cuando una tarea vence en menos de 24 horas
- 🚨 **Alerta 3 horas** — notificación urgente cuando quedan menos de 3 horas
- ⏰ **systemd timer** — ejecuta cada 3 horas automáticamente en segundo plano
- 🌟 **100% en español** — todas las notificaciones y la interfaz de usuario en español

---

## 🚀 Instalación Rápida

### Prerrequisitos

- Python 3.11+
- Git
- systemd (Linux)
- Un canal de Discord donde crear un webhook

### Pasos

```bash
# 1. Clonar el repositorio
git clone https://github.com/loonbac/Blackboard-Notify-DscWebhook.git
cd Blackboard-Notify-DscWebhook

# 2. Crear y activar entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Instalar navegador Playwright (requerido por el scraper)
playwright install chromium

# 5. Configurar credenciales
cp .env.example .env
nano .env   # o tu editor preferido
```

### Configurar `.env`

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `BLACKBOARD_URL` | URL de tu Blackboard | `https://senati.blackboard.com` |
| `BLACKBOARD_USER` | Tu correo institucional | `1531276@senati.pe` |
| `BLACKBOARD_PASS` | Tu contraseña | `tu_contraseña_segura` |
| `DISCORD_WEBHOOK_URL` | URL del webhook de Discord | `https://discord.com/api/webhooks/...` |
| `WEEKLY_DIGEST_DAY` | Día del digest semanal (1=lunes, 0=domingo) | `1` |
| `TIMEZONE` | Zona horaria IANA para fechas | `America/Lima` |
| `HEADLESS` | Modo headless del navegador | `true` |

### Crear un Webhook en Discord

1. Ve al canal de Discord donde quieres recibir las alertas.
2. Haz clic en **⚙️ Configuración del canal → Integraciones → Webhooks**.
3. Crea un webhook nuevo y copia la **URL**.
4. Pégala en `DISCORD_WEBHOOK_URL` en tu archivo `.env`.

---

## 🏃 Uso

### Probar manualmente

```bash
source .venv/bin/activate
python bot.py
```

El bot ejecutará el ciclo completo de scraping y notificaciones una vez. Revisa la salida en consola para verificar que todo funcione.

### Instalar como servicio systemd (producción)

```bash
# Copiar archivos de servicio al directorio de systemd
sudo cp blackboard-bot.service blackboard-bot.timer /etc/systemd/system/

# Recargar daemon y habilitar el timer
sudo systemctl daemon-reload
sudo systemctl enable --now blackboard-bot.timer

# Verificar estado
systemctl status blackboard-bot.timer
```

### Timer: cada 3 horas

El archivo `blackboard-bot.timer` está configurado para ejecutar el bot a las:

```
00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00
```

Esto equivale a cada 3 horas todos los días.

### Ver logs

```bash
# Logs del servicio
sudo journalctl -u blackboard-bot.service -f

# Logs del timer
sudo journalctl -u blackboard-bot.timer -f
```

---

## 🏗️ Arquitectura

```
blackboard/
├── bot.py                      # Orquestador principal — punto de entrada
├── config.py                   # Carga y validación de configuración desde .env
├── blackboard_scraper.py       # Cliente Playwright — login O365 y scraping Ultra
├── database.py                 # Base de datos SQLite — seguimiento de tareas
├── discord_notifier.py         # Envío de webhooks a Discord con reintentos
├── notified_cache.py           # (legacy — mantenido por compatibilidad)
│
├── tests/                      # Suite de tests
│   ├── test_config.py          # Tests de validación de configuración
│   ├── test_database.py        # Tests de base de datos SQLite
│   ├── test_discord_notifier.py# Tests de construcción y envío de embeds
│   ├── test_blackboard_scraper.py# Tests del scraper Playwright
│   └── test_bot.py             # Tests de lógica del orquestador
│
├── blackboard-bot.service      # Definición de servicio systemd (Type=oneshot)
├── blackboard-bot.timer        # Timer systemd — ejecuta cada 3 horas
│
├── requirements.txt            # Dependencias Python
├── .env.example                # Plantilla de configuración
├── session.json                # (generado) Cookies de sesión para evitar re-login
└── notified_assignments.db     # (generado) Base de datos SQLite con seguimiento
```

### Diagrama de componentes

```
                    ┌─────────────────────────────────────┐
                    │           systemd timer              │
                    │        (cada 3 horas)                │
                    └──────────────┬──────────────────────┘
                                   │ ejecuta
                                   ▼
                          ┌────────────────┐
                          │    bot.py       │
                          │  (orquestador)  │
                          └───────┬─────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
  ┌────────────┐          ┌───────────────┐        ┌───────────────┐
  │  config.py │          │   database.py  │        │blackboard_    │
  │  (config)  │          │   (SQLite)    │        │ scraper.py    │
  └────────────┘          └───────────────┘        │  (Playwright) │
                                                  └───────────────┘
                                 │                        │
                                 │   scrape + upsert      │
                                 ▼                        ▼
                          ┌───────────────┐        ┌───────────────┐
                          │  Discord      │◄───────│  Blackboard  │
                          │  webhook      │        │  (O365/SAML) │
                          └───────────────┘        └───────────────┘
```

---

## 📊 Flujo de Ejecución

```
[systemd timer] ── cada 3h ──→ bot.py (main asíncrono)
                                  │
                                  ├── 1.  Cargar configuración desde .env
                                  ├── 2.  Abrir base de datos SQLite
                                  ├── 3.  Migrar desde JSON cache si existe
                                  ├── 4.  Login a Blackboard via O365/SAML
                                  ├── 5.  Scrape → lista de Assignment
                                  ├── 6.  Upsert todas las tareas en DB
                                  │         ↳ detectar cambios de fecha → 📅
                                  ├── 7.  Tareas nuevas → 🆕 Notificación
                                  ├── 8.  Si es lunes → 📋 Digest semanal
                                  ├── 9.  Tareas < 24h → 🔴 Alerta 24h
                                  ├── 10. Tareas < 3h  → 🚨 Alerta 3h
                                  └── 11. Cerrar scraper y DB
```

---

## 🎨 Colores de las Notificaciones

| Tipo de notificación | Emoji | Color | Hex |
|---------------------|-------|-------|-----|
| 📋 Digest semanal | `📋` | Azul | `#344703` |
| 🆕 Nueva tarea | `🆕` | Púrpura | `#800080` |
| 📅 Fecha actualizada | `📅` | Amarillo | `#F1C40F` |
| 🔴 Alerta 24h | `⏰` | Rojo | `#FF0000` |
| 🚨 Alerta 3h | `🚨` | Naranja | `#E67E22` |

---

## 🔐 Login con Microsoft O365

El bot soporta el flujo completo de autenticación de SENATI:

1. Navega a Blackboard y detecta el botón **"Ingresa con tu correo @senati.pe"** (O365/SAML).
2. Realiza redirect a Microsoft Entra ID (Azure AD).
3. Ingresa credenciales en el formulario de Microsoft.
4. Maneja el prompt **"¿Mantener sesión iniciada?"**.
5. Detecta login exitoso y guarda cookies en `session.json` para evitar re-autenticación en ejecuciones futuras.

---

## 📄 Licencia

**PolyForm Noncommercial License 1.0.0** — ver [`LICENSE`](LICENSE)

- ✅ Uso gratuito para fines no comerciales
- ✅ Modificable y distribuible manteniendo créditos y avisos de copyright
- ❌ Prohibido su uso con fines comerciales

Para uso comercial, contactar al autor.

---

## 👤 Autor

**Joshua Rosales** — [@loonbac](https://github.com/loonbac)

---

## 🧪 Tests

```bash
# Activar entorno virtual
source .venv/bin/activate

# Ejecutar todos los tests
python -m pytest tests/ -v

# Tests específicos por módulo
python -m pytest tests/test_config.py -v
python -m pytest tests/test_database.py -v
python -m pytest tests/test_discord_notifier.py -v
python -m pytest tests/test_blackboard_scraper.py -v
python -m pytest tests/test_bot.py -v

# Tests con coverage
python -m pytest tests/ -v --tb=short
```

La suite incluye **354 tests** cubriendo configuración, base de datos, scraper, lógica del orquestador y construcción de notificaciones Discord.
