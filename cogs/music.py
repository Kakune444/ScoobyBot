import asyncio
import base64
import binascii
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from cogs.scooby_quotes import scooby_quote

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COOKIES_PATH = PROJECT_ROOT / "cookies.txt"
DEFAULT_DIAGNOSTIC_URL = "https://www.youtube.com/watch?v=msa8KUwXbz0"
MAX_COOKIE_FILE_BYTES = 10 * 1024 * 1024

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "ignoreerrors": False,
    "socket_timeout": 15,
    "retries": 2,
    "extractor_retries": 2,
    "fragment_retries": 2,
}


def _cookie_validation(data: bytes) -> tuple[bool, str, int]:
    """Validate Netscape cookies without ever returning a cookie value."""
    if not data:
        return False, "empty", 0
    if len(data) > MAX_COOKIE_FILE_BYTES:
        return False, "too_large", 0

    has_header = False
    cookie_rows = 0
    for raw_line in data.splitlines():
        # Preserve trailing tabs: an empty cookie value is valid Netscape data.
        line = raw_line.rstrip(b"\r\n").lstrip(b"\xef\xbb\xbf")
        if not line.strip():
            continue
        lowered = line.lower()
        if lowered.startswith((b"# netscape http cookie file", b"# http cookie file")):
            has_header = True
            continue
        if line.startswith(b"#HttpOnly_"):
            line = line[len(b"#HttpOnly_"):]
        elif line.startswith(b"#"):
            continue

        fields = line.split(b"\t")
        if len(fields) != 7:
            return False, "invalid_columns", cookie_rows
        if not fields[0] or fields[1] not in (b"TRUE", b"FALSE"):
            return False, "invalid_domain_flags", cookie_rows
        if not fields[2].startswith(b"/"):
            return False, "invalid_path", cookie_rows
        if fields[3] not in (b"TRUE", b"FALSE"):
            return False, "invalid_secure_flag", cookie_rows
        try:
            int(fields[4])
        except ValueError:
            return False, "invalid_expiration", cookie_rows
        if not fields[5]:
            return False, "invalid_name", cookie_rows
        cookie_rows += 1

    if not has_header:
        return False, "missing_netscape_header", cookie_rows
    if not cookie_rows:
        return False, "no_cookie_entries", 0
    return True, "valid_netscape", cookie_rows


def _inspect_cookie_file(path: Path, *, source: str) -> dict:
    status = {
        "source": source,
        "path": str(path),
        "exists": False,
        "size_bytes": 0,
        "format": "absent",
        "configured_in_ytdlp": False,
    }
    try:
        if not path.is_file():
            return status
        status["exists"] = True
        status["size_bytes"] = path.stat().st_size
        if status["size_bytes"] > MAX_COOKIE_FILE_BYTES:
            status["format"] = "too_large"
            return status
        valid, reason, _ = _cookie_validation(path.read_bytes())
        status["format"] = "valid_netscape" if valid else reason
    except (OSError, ValueError):
        status["format"] = "unreadable"
    return status


def _resolve_cookie_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve(strict=False)


def _configure_youtube_auth() -> dict:
    """Configure cookies and return metadata safe for logs/diagnostics."""
    encoded_cookies = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
    if encoded_cookies:
        try:
            cookie_data = base64.b64decode("".join(encoded_cookies.split()), validate=True)
            valid, reason, _ = _cookie_validation(cookie_data)
            if not valid:
                print(f"[youtube] cookies source=YOUTUBE_COOKIES_B64 status={reason}")
                return {
                    "source": "YOUTUBE_COOKIES_B64",
                    "path": None,
                    "exists": True,
                    "size_bytes": len(cookie_data),
                    "format": reason,
                    "configured_in_ytdlp": False,
                }

            temporary_cookie_file = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="scoobybot-youtube-",
                suffix=".txt",
                delete=False,
            )
            with temporary_cookie_file:
                temporary_cookie_file.write(cookie_data)
            os.chmod(temporary_cookie_file.name, 0o600)
            cookie_file = Path(temporary_cookie_file.name).resolve()
            YTDL_OPTIONS["cookiefile"] = str(cookie_file)
            status = {
                "source": "YOUTUBE_COOKIES_B64",
                "path": str(cookie_file),
                "exists": True,
                "size_bytes": len(cookie_data),
                "format": "valid_netscape",
                "configured_in_ytdlp": True,
            }
            print(
                f"[youtube] cookies source=YOUTUBE_COOKIES_B64 status=valid_netscape "
                f"size={len(cookie_data)} configured=True"
            )
            return status
        except (binascii.Error, ValueError, OSError):
            print("[youtube] cookies source=YOUTUBE_COOKIES_B64 status=invalid_base64")
            return {
                "source": "YOUTUBE_COOKIES_B64",
                "path": None,
                "exists": True,
                "size_bytes": 0,
                "format": "invalid_base64",
                "configured_in_ytdlp": False,
            }

    configured_path = os.getenv("YOUTUBE_COOKIES_FILE")
    source = "YOUTUBE_COOKIES_FILE" if configured_path else "default_cookies.txt"
    cookie_file = _resolve_cookie_path(configured_path) if configured_path else DEFAULT_COOKIES_PATH
    status = _inspect_cookie_file(cookie_file, source=source)
    if status["format"] == "valid_netscape":
        YTDL_OPTIONS["cookiefile"] = str(cookie_file)
        status["configured_in_ytdlp"] = True
    print(
        f"[youtube] cookies source={source} path={cookie_file} "
        f"status={status['format']} size={status['size_bytes']} "
        f"configured={status['configured_in_ytdlp']}"
    )
    return status


def _safe_provider_label(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}"


def _configure_pot_provider() -> dict:
    """Configure bgutil's HTTP provider without logging its full URL."""
    raw_url = os.getenv("YOUTUBE_POT_BASE_URL", "").strip().rstrip("/")
    try:
        plugin_installed = bool(importlib.util.find_spec("yt_dlp_plugins.extractor.getpot_bgutil_http"))
    except (ImportError, ModuleNotFoundError, ValueError):
        plugin_installed = False
    status = {
        "configured": False,
        "label": None,
        "plugin_installed": plugin_installed,
        "base_url": None,
        "health": "not_configured",
    }
    if not raw_url:
        return status

    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        print("[youtube] PO provider status=invalid_configuration")
        status["health"] = "invalid_configuration"
        return status

    YTDL_OPTIONS.setdefault("extractor_args", {})[
        "youtubepot-bgutilhttp"
    ] = [f"base_url={raw_url}"]
    # The current PO Token guide recommends mweb for the provider-backed flow.
    YTDL_OPTIONS.setdefault("extractor_args", {}).setdefault(
        "youtube", {}
    )["player_client"] = ["mweb"]
    status.update({
        "configured": True,
        "label": _safe_provider_label(raw_url),
        "base_url": raw_url,
        "health": "not_checked",
    })
    print(
        f"[youtube] PO provider label={status['label']} "
        f"plugin_installed={status['plugin_installed']} configured=True"
    )
    return status


COOKIE_STATUS = _configure_youtube_auth()
POT_STATUS = _configure_pot_provider()

user_agent = os.getenv("YOUTUBE_USER_AGENT")
if user_agent:
    YTDL_OPTIONS["http_headers"] = {"User-Agent": user_agent}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

INACTIVITY_TIMEOUT_SECONDS = 10 * 60


class _DiagnosticLogger:
    """Collect only safe yt-dlp signals; never forwards verbose output verbatim."""

    def __init__(self):
        self.signals: set[str] = set()

    def _capture(self, message) -> None:
        text = str(message).lower()
        if "po token providers: none" in text:
            self.signals.add("po_none")
        if "bgutil" in text and "provider" in text:
            self.signals.add("po_bgutil")
        if "login_required" in text or "sign in to confirm" in text:
            self.signals.add("auth_challenge")
        if "no supported javascript runtime" in text or "yt_dlp_ejs" in text:
            self.signals.add("js_runtime")
        if "video unavailable" in text or "private video" in text:
            self.signals.add("video_unavailable")
        if "timed out" in text or "connection" in text:
            self.signals.add("network")

    def debug(self, message, *args, **kwargs):
        self._capture(message)

    def info(self, message, *args, **kwargs):
        self._capture(message)

    def warning(self, message, *args, **kwargs):
        self._capture(message)

    def error(self, message, *args, **kwargs):
        self._capture(message)


def _command_version(executable: str | None, *args: str) -> str | None:
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)
    return first_line[:120] if first_line else None


def _probe_pot_provider(base_url: str | None) -> tuple[str, str | None]:
    if not base_url:
        return "not_configured", None
    try:
        request = Request(
            f"{base_url.rstrip('/')}/ping",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
        version = payload.get("version") if isinstance(payload, dict) else None
        return ("reachable", str(version)[:32] if version else None)
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return "unreachable", None


def _classify_youtube_failure(
    error: BaseException,
    logger: _DiagnosticLogger,
    cookie_status: dict,
    pot_status: dict,
) -> str:
    text = f"{type(error).__name__} {error}".lower()
    signals = logger.signals
    if cookie_status["format"] not in {"valid_netscape", "absent"}:
        return "cookies_invalid_format"
    if pot_status["configured"] and (
        not pot_status["plugin_installed"]
        or pot_status.get("health") == "unreachable"
        or "po_none" in signals
        or "bgutil" in text
    ):
        return "po_token_provider_unavailable"
    if "js_runtime" in signals or "no supported javascript runtime" in text or "yt_dlp_ejs" in text:
        return "ytdlp_runtime_or_ejs"
    if "auth_challenge" in signals or "login_required" in text or "sign in to confirm" in text:
        return "cookies_absent" if cookie_status["format"] == "absent" else "cookies_rejected_or_expired"
    if "video_unavailable" in signals or "private video" in text or "age-restricted" in text:
        return "youtube_video_unavailable"
    if "network" in signals or any(token in text for token in ("timed out", "connection", "dns", "name or service")):
        return "railway_network"
    return "youtube_or_ytdlp"


def _friendly_youtube_error(category: str) -> str:
    return {
        "cookies_invalid_format": "Le fichier cookies YouTube est absent, illisible ou n'est pas au format Netscape.",
        "cookies_absent": "YouTube demande une authentification : aucun cookie valide n'est chargé sur le bot.",
        "cookies_rejected_or_expired": "YouTube rejette les cookies chargés : ils sont probablement expirés ou invalides.",
        "po_token_provider_unavailable": "Le PO Token Provider n'est pas disponible ou ne répond pas sur Railway.",
        "ytdlp_runtime_or_ejs": "Une dépendance yt-dlp/EJS ou le runtime JavaScript est indisponible.",
        "youtube_video_unavailable": "Cette vidéo est indisponible, privée ou non accessible depuis YouTube.",
        "railway_network": "Railway n'arrive pas à joindre YouTube ou le provider PO Token temporairement.",
        "youtube_or_ytdlp": "YouTube ou yt-dlp a refusé l'extraction de cette vidéo.",
    }.get(category, "Impossible d'extraire cette vidéo YouTube.")


def _diagnostic_summary(logger: _DiagnosticLogger, extraction_ok: bool, error_category: str | None) -> str:
    cookie_format = COOKIE_STATUS["format"]
    cookie_line = (
        f"valide, {COOKIE_STATUS['size_bytes']} octets"
        if cookie_format == "valid_netscape"
        else cookie_format
    )
    provider_line = "non configuré"
    if POT_STATUS["configured"]:
        provider_line = f"{POT_STATUS['health']}"
        if not POT_STATUS["plugin_installed"]:
            provider_line += ", plugin Python absent"
        if "po_bgutil" in logger.signals:
            provider_line += ", détecté par yt-dlp"
        elif not extraction_ok:
            provider_line += ", non détecté dans les signaux"

    deno_path = os.getenv("YOUTUBE_DENO_PATH") or shutil.which("deno")
    deno_version = _command_version(deno_path, "--version")
    ffmpeg_path = shutil.which("ffmpeg")
    ffmpeg_version = _command_version(ffmpeg_path, "-version")
    ytdlp_version = getattr(getattr(yt_dlp, "version", None), "__version__", "inconnue")
    railway = "oui" if os.getenv("RAILWAY_ENVIRONMENT_NAME") else "non/local"
    result_line = "OK (metadata/format extraction, aucun téléchargement complet)" if extraction_ok else error_category

    return "\n".join([
        "🔎 Diagnostic yt-dlp YouTube",
        f"• yt-dlp={ytdlp_version} | Python={os.sys.version_info.major}.{os.sys.version_info.minor}",
        f"• A cookies: {cookie_format} ({cookie_line})",
        f"• B cookies rejetés/expirés: {'à suspecter' if error_category == 'cookies_rejected_or_expired' else 'non détecté'}",
        f"• C PO Token: {provider_line}",
        f"• D yt-dlp/EJS: Deno={'OK' if deno_version else 'absent'} | ffmpeg={'OK' if ffmpeg_version else 'absent'}",
        f"• E YouTube/extraction: {result_line}",
        f"• F Railway: {railway} | cookiefile configuré={COOKIE_STATUS['configured_in_ytdlp']}",
        f"• Signaux sûrs: {', '.join(sorted(logger.signals)) or 'aucun'}",
    ])[:1900]


def _is_youtube_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))
    )


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = {}
        self.inactivity_tasks = {}
        self._ytdl_lock = asyncio.Lock()

    def cog_unload(self):
        for task in self.inactivity_tasks.values():
            task.cancel()
        self.inactivity_tasks.clear()

    def _queue(self, guild_id):
        return self.queues.setdefault(guild_id, [])

    def _cancel_inactivity_timer(self, guild_id):
        task = self.inactivity_tasks.pop(guild_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _reset_inactivity_timer(self, guild: discord.Guild, channel: discord.abc.Messageable):
        self._cancel_inactivity_timer(guild.id)
        self.inactivity_tasks[guild.id] = asyncio.create_task(self._inactivity_watchdog(guild, channel))

    async def _inactivity_watchdog(self, guild: discord.Guild, channel: discord.abc.Messageable):
        try:
            await asyncio.sleep(INACTIVITY_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        self.inactivity_tasks.pop(guild.id, None)
        voice_client = guild.voice_client
        if voice_client is None or (voice_client.is_playing() and not voice_client.is_paused()):
            return

        self._queue(guild.id).clear()
        await voice_client.disconnect()
        try:
            await channel.send(f"👋 Déconnecté du vocal après 10 minutes d'inactivité.\n💬 *{scooby_quote()}*")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id or after.channel is not None:
            return
        self._cancel_inactivity_timer(member.guild.id)
        self._queue(member.guild.id).clear()

    async def _extract(self, query):
        loop = asyncio.get_running_loop()
        async with self._ytdl_lock:
            data = await loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(query, download=False),
            )
        if not data:
            raise RuntimeError("yt-dlp returned no result")
        if "entries" in data:
            entries = data.get("entries") or []
            data = next((entry for entry in entries if entry), None)
        if not data or not data.get("url"):
            raise RuntimeError("yt-dlp returned no playable audio")
        return {"title": data.get("title", "Titre inconnu"), "url": data["url"]}

    async def _play_next(self, guild: discord.Guild, channel: discord.abc.Messageable):
        queue = self._queue(guild.id)
        voice_client = guild.voice_client
        if voice_client is None:
            return
        if not queue:
            self._reset_inactivity_timer(guild, channel)
            return

        track = queue.pop(0)

        try:
            source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
        except discord.ClientException as e:
            print(f"Erreur FFmpeg (création source) : {type(e).__name__}")
            await channel.send(f"❌ Impossible de lancer **{track['title']}** avec FFmpeg.")
            return

        def after(error):
            if error:
                print(f"Erreur de lecture : {type(error).__name__}")
                asyncio.run_coroutine_threadsafe(
                    channel.send(f"⚠️ La lecture de **{track['title']}** s'est interrompue."),
                    self.bot.loop,
                )

            fut = asyncio.run_coroutine_threadsafe(self._play_next(guild, channel), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Erreur après lecture : {type(e).__name__}")
                asyncio.run_coroutine_threadsafe(
                    channel.send("❌ Erreur lors du passage au morceau suivant."),
                    self.bot.loop,
                )

        voice_client.play(source, after=after)
        self._cancel_inactivity_timer(guild.id)
        await channel.send(f"▶️ Lecture : **{track['title']}**\n💬 *{scooby_quote()}*")

    @app_commands.command(name="join", description="Faire rejoindre le bot dans un salon vocal")
    @app_commands.describe(channel="Le salon vocal à rejoindre (par défaut : ton salon vocal actuel)")
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction, channel: discord.VoiceChannel = None):
        if channel is None:
            if interaction.user.voice is None:
                await interaction.response.send_message(
                    "❌ Tu dois être dans un salon vocal, ou préciser le paramètre `channel`.",
                    ephemeral=True,
                )
                return
            channel = interaction.user.voice.channel

        await interaction.response.defer()

        try:
            voice_client = interaction.guild.voice_client
            if voice_client is not None:
                await voice_client.move_to(channel)
            else:
                await channel.connect()
        except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as e:
            await interaction.followup.send(f"❌ Impossible de rejoindre {channel.mention} : {e}")
            return

        voice_client = interaction.guild.voice_client
        if not voice_client.is_playing():
            self._reset_inactivity_timer(interaction.guild, interaction.channel)

        await interaction.followup.send(f"✅ Rejoint **{channel.name}**.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="play", description="Jouer ou ajouter un morceau à la file d'attente")
    @app_commands.describe(recherche="Titre, artiste ou URL YouTube à jouer")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, recherche: str):
        if interaction.guild.voice_client is None and interaction.user.voice is None:
            await interaction.response.send_message("❌ Tu dois être dans un salon vocal.", ephemeral=True)
            return

        await interaction.response.defer()

        if interaction.guild.voice_client is None:
            try:
                await interaction.user.voice.channel.connect()
            except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError) as e:
                await interaction.followup.send(f"❌ Impossible de rejoindre ton salon vocal : {e}")
                return

        try:
            track = await self._extract(recherche)
        except Exception as e:
            category = _classify_youtube_failure(e, _DiagnosticLogger(), COOKIE_STATUS, POT_STATUS)
            print(f"Erreur yt-dlp catégorie={category} type={type(e).__name__}")
            await interaction.followup.send(
                f"❌ Impossible de trouver ce morceau. {_friendly_youtube_error(category)}"
            )
            return

        queue = self._queue(interaction.guild.id)
        queue.append(track)

        voice_client = interaction.guild.voice_client
        if voice_client.is_playing() or voice_client.is_paused():
            await interaction.followup.send(f"➕ Ajouté à la file : **{track['title']}**\n💬 *{scooby_quote()}*")
        else:
            await self._play_next(interaction.guild, interaction.channel)
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass

    @app_commands.command(name="ytdlpdiagnostic", description="Diagnostiquer yt-dlp et YouTube")
    @app_commands.describe(url="URL YouTube à tester, par défaut la vidéo de diagnostic")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def ytdlpdiagnostic(
        self,
        interaction: discord.Interaction,
        url: str = DEFAULT_DIAGNOSTIC_URL,
    ):
        """Teste les métadonnées/URLs de format sans télécharger le média."""
        if not _is_youtube_url(url):
            await interaction.response.send_message(
                "❌ Fournis une URL YouTube (`youtube.com` ou `youtu.be`).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_running_loop()
        provider_health, provider_version = await loop.run_in_executor(
            None,
            _probe_pot_provider,
            POT_STATUS.get("base_url"),
        )
        if POT_STATUS["configured"]:
            POT_STATUS["health"] = provider_health

        logger = _DiagnosticLogger()
        diagnostic_options = dict(YTDL_OPTIONS)
        diagnostic_options.update({
            "quiet": True,
            "verbose": True,
            "skip_download": True,
            "noplaylist": True,
            "logger": logger,
        })
        extraction_ok = False
        category = None
        try:
            diagnostic_ytdl = yt_dlp.YoutubeDL(diagnostic_options)
            info = await loop.run_in_executor(
                None,
                lambda: diagnostic_ytdl.extract_info(url, download=False),
            )
            extraction_ok = bool(info and (info.get("url") or info.get("formats")))
            if not extraction_ok:
                raise RuntimeError("yt-dlp returned no metadata")
        except Exception as error:
            category = _classify_youtube_failure(error, logger, COOKIE_STATUS, POT_STATUS)
            print(f"Diagnostic yt-dlp catégorie={category} type={type(error).__name__}")

        summary = _diagnostic_summary(logger, extraction_ok, category)
        if provider_version:
            summary += f"\n• Version provider HTTP: {provider_version}"
        if category:
            summary += f"\n• Cause probable: {_friendly_youtube_error(category)}"
        await interaction.followup.send(summary[:1950], ephemeral=True)

    @app_commands.command(name="queue", description="Afficher la file d'attente actuelle")
    @app_commands.guild_only()
    async def queue_cmd(self, interaction: discord.Interaction):
        queue = self._queue(interaction.guild.id)
        if not queue:
            await interaction.response.send_message("La file d'attente est vide.", ephemeral=True)
            return

        lines = [f"{i + 1}. {t['title']}" for i, t in enumerate(queue)]
        embed = discord.Embed(title="File d'attente", description="\n".join(lines), color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skip", description="Passer au morceau suivant")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message("❌ Rien n'est en cours de lecture.", ephemeral=True)
            return
        voice_client.stop()
        await interaction.response.send_message(f"⏭️ Morceau suivant.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="pause", description="Mettre la lecture en pause")
    @app_commands.guild_only()
    async def pause(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message("❌ Rien n'est en cours de lecture.", ephemeral=True)
            return
        voice_client.pause()
        self._reset_inactivity_timer(interaction.guild, interaction.channel)
        await interaction.response.send_message(f"⏸️ Lecture en pause.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="resume", description="Reprendre la lecture en pause")
    @app_commands.guild_only()
    async def resume(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_paused():
            await interaction.response.send_message("❌ Rien n'est en pause.", ephemeral=True)
            return
        voice_client.resume()
        self._cancel_inactivity_timer(interaction.guild.id)
        await interaction.response.send_message(f"▶️ Reprise de la lecture.\n💬 *{scooby_quote()}*")

    @app_commands.command(name="leave", description="Déconnecter le bot du salon vocal")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if voice_client is None:
            await interaction.response.send_message("❌ Je ne suis pas connecté à un salon vocal.", ephemeral=True)
            return
        self._cancel_inactivity_timer(interaction.guild.id)
        self._queue(interaction.guild.id).clear()
        await voice_client.disconnect()
        await interaction.response.send_message(f"👋 Déconnecté du vocal.\n💬 *{scooby_quote()}*")


async def setup(bot):
    await bot.add_cog(Music(bot))
