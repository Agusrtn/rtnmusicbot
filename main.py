import discord
import yt_dlp as youtube_dl
import os
from dotenv import load_dotenv
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)

BOT_VERSION = (
    os.getenv('BOT_VERSION')
    or os.getenv('RENDER_GIT_COMMIT')
    or os.getenv('GIT_COMMIT')
    or 'unknown'
)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


def start_healthcheck_server():
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN no está configurado. Define la variable de entorno en Fly.io.")

# Escribir cookies de YouTube a archivo temporal si están disponibles
COOKIES_FILE = None
youtube_cookies = os.getenv('YOUTUBE_COOKIES')
if youtube_cookies:
    import tempfile, base64
    try:
        cookies_data = base64.b64decode(youtube_cookies).decode('utf-8')
    except Exception:
        cookies_data = youtube_cookies
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    tmp.write(cookies_data)
    tmp.close()
    COOKIES_FILE = tmp.name
    logging.info(f"✅ Cookies de YouTube cargadas desde variable de entorno")

# Configuración del bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# Si defines DISCORD_GUILD_ID, los slash commands aparecen casi al instante en ese servidor.
guild_ids_env = os.getenv('DISCORD_GUILD_ID', '').strip()
debug_guilds = []
if guild_ids_env:
    for raw_id in guild_ids_env.split(','):
        raw_id = raw_id.strip()
        if raw_id.isdigit():
            debug_guilds.append(int(raw_id))

if debug_guilds:
    logging.info(f"🧪 Registrando comandos en guilds de debug: {debug_guilds}")
    bot = discord.Bot(intents=intents, debug_guilds=debug_guilds)
else:
    bot = discord.Bot(intents=intents)

# Configuración de yt-dlp para streaming directo (sin descargar)
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'ignoreerrors': False,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['android_music', 'android', 'web'],
        }
    },
}

# Opciones de FFmpeg para streaming (reconexión automática)
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

if COOKIES_FILE:
    YTDL_OPTIONS['cookiefile'] = COOKIES_FILE


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or bot.loop

        def _extract():
            with youtube_dl.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'entries' in info:
                    info = next((e for e in info['entries'] if e), None)
                if not info:
                    raise RuntimeError("No se pudo obtener información del video")
                return info

        data = await loop.run_in_executor(None, _extract)
        stream_url = data.get('url')
        if not stream_url:
            raise RuntimeError("No se pudo obtener la URL de streaming")

        logging.info(f"🎵 Stream URL obtenida para: {data.get('title')}")
        ffmpeg_source = discord.FFmpegPCMAudio(stream_url, **FFMPEG_OPTIONS)
        return cls(ffmpeg_source, data=data)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} está listo!')
    try:
        await bot.sync_commands()
        logging.info("🔄 Slash commands sincronizados")
    except Exception as e:
        logging.warning(f"No se pudieron sincronizar slash commands: {e}")


async def safe_reply(ctx: discord.ApplicationContext, content: str, ephemeral: bool = False):
    try:
        await ctx.followup.send(content, ephemeral=ephemeral)
    except Exception:
        try:
            await ctx.respond(content, ephemeral=ephemeral)
        except Exception:
            try:
                await ctx.channel.send(content)
            except Exception:
                pass


@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
    logging.exception("Error en comando de aplicacion", exc_info=error)
    await safe_reply(ctx, "❌ Ocurrio un error al ejecutar el comando.", ephemeral=True)

@bot.slash_command(name="play", description="Reproduce música de YouTube")
async def play(ctx: discord.ApplicationContext, cancion: str):
    """
    Reproduce una canción de YouTube en el canal de voz
    """
    # Acknowledge inmediato para evitar timeout de interaccion.
    try:
        await ctx.defer()
    except Exception:
        pass

    if not ctx.author or not ctx.author.voice:
        await safe_reply(ctx, "❌ Debes estar en un canal de voz para usar este comando.", ephemeral=True)
        return

    await safe_reply(ctx, f"🎵 Buscando: **{cancion}**...")

    channel = ctx.author.voice.channel

    try:
        voice_client = ctx.guild.voice_client
        if voice_client and voice_client.channel != channel:
            await voice_client.move_to(channel)
        elif not voice_client:
            voice_client = await channel.connect()
    except Exception as e:
        await safe_reply(ctx, f"❌ Error al conectarme al canal de voz: {e}")
        return
    
    try:
        # Obtener stream directo y reproducir
        source = await YTDLSource.from_url(cancion, loop=bot.loop)
        
        # Reproducir la canción
        def after_play(e):
            if e:
                logging.error(f'Error durante reproducción: {e}')

        voice_client.play(source, after=after_play)
        await safe_reply(ctx, f"▶️ Reproduciendo: **{source.title}**")
    
    except Exception as e:
        await safe_reply(ctx, f"❌ Error al reproducir la canción: {str(e)}")

@bot.slash_command(name="stop", description="Detiene la reproducción de música")
async def stop(ctx: discord.ApplicationContext):
    """
    Detiene la reproducción y desconecta del canal de voz
    """
    if not ctx.guild.voice_client:
        await ctx.respond("❌ El bot no está en un canal de voz.", ephemeral=True)
        return
    
    voice_client = ctx.guild.voice_client
    voice_client.stop()
    await voice_client.disconnect()
    await ctx.respond("⏹️ Música detenida y bot desconectado.")

@bot.slash_command(name="pause", description="Pausa la música")
async def pause(ctx: discord.ApplicationContext):
    """
    Pausa la reproducción de música
    """
    if not ctx.guild.voice_client:
        await ctx.respond("❌ El bot no está reproduciendo nada.", ephemeral=True)
        return
    
    voice_client = ctx.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
        await ctx.respond("⏸️ Música pausada.")
    else:
        await ctx.respond("❌ El bot no está reproduciendo nada.", ephemeral=True)

@bot.slash_command(name="resume", description="Reanuda la música")
async def resume(ctx: discord.ApplicationContext):
    """
    Reanuda la reproducción de música
    """
    if not ctx.guild.voice_client:
        await ctx.respond("❌ El bot no está en un canal de voz.", ephemeral=True)
        return
    
    voice_client = ctx.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
        await ctx.respond("▶️ Música reanudada.")
    else:
        await ctx.respond("❌ La música no está pausada.", ephemeral=True)

@bot.slash_command(name="help", description="Muestra los comandos disponibles")
async def help_command(ctx: discord.ApplicationContext):
    """
    Muestra la lista de comandos disponibles
    """
    embed = discord.Embed(title="🎵 Comandos Disponibles", color=discord.Color.blue())
    embed.add_field(name="/play <canción>", value="Reproduce una canción de YouTube", inline=False)
    embed.add_field(name="/pause", value="Pausa la música actual", inline=False)
    embed.add_field(name="/resume", value="Reanuda la música pausada", inline=False)
    embed.add_field(name="/stop", value="Detiene la música y desconecta del canal", inline=False)
    embed.add_field(name="/formatos <url>", value="Muestra formatos disponibles detectados por yt-dlp", inline=False)
    embed.add_field(name="/help", value="Muestra este mensaje de ayuda", inline=False)
    
    await ctx.respond(embed=embed, ephemeral=True)


@bot.slash_command(name="formatos", description="Lista formatos disponibles de un video de YouTube")
async def formatos(ctx: discord.ApplicationContext, url: str):
    try:
        await ctx.defer(ephemeral=True)
    except Exception:
        pass

    loop = bot.loop

    def _extract_formats():
        info_opts = dict(YTDL_OPTIONS)
        info_opts.pop('format', None)
        with youtube_dl.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = next((e for e in info['entries'] if e), None)
            if not info:
                raise RuntimeError("No se pudo obtener información del video")

        formats = info.get('formats') or []
        audio_formats = [f for f in formats if f.get('acodec') not in (None, 'none') and f.get('url')]
        audio_formats = sorted(audio_formats, key=lambda f: f.get('abr') or f.get('tbr') or 0, reverse=True)
        return info, audio_formats

    try:
        info, audio_formats = await loop.run_in_executor(None, _extract_formats)
        if not audio_formats:
            await safe_reply(ctx, "❌ No se detectaron formatos con audio para ese video.", ephemeral=True)
            return

        lines = []
        for f in audio_formats[:15]:
            lines.append(
                f"id={f.get('format_id')} | ext={f.get('ext')} | acodec={f.get('acodec')} | "
                f"vcodec={f.get('vcodec')} | abr={f.get('abr')} | tbr={f.get('tbr')}"
            )

        title = info.get('title', 'Sin título')
        text = f"🎵 **{title}**\nFormatos detectados (top 15):\n" + "\n".join(lines)

        if len(text) > 1900:
            text = text[:1900] + "\n..."
        await safe_reply(ctx, text, ephemeral=True)
    except Exception as e:
        await safe_reply(ctx, f"❌ Error al listar formatos: {e}", ephemeral=True)

# Ejecutar el bot
if __name__ == "__main__":
    start_healthcheck_server()
    logging.info(f"🚀 Iniciando RTN MUSIC BOT | version={BOT_VERSION}")

    retry_seconds = int(os.getenv("DISCORD_RETRY_SECONDS", "180"))
    while True:
        try:
            bot.run(TOKEN)
            break
        except Exception as e:
            # Evita que Render reinicie en bucle rapido cuando Discord responde 1015/401/429.
            logging.error(f"❌ Error al iniciar sesion en Discord: {e}")
            logging.info(f"⏳ Reintentando conexion en {retry_seconds} segundos...")
            time.sleep(retry_seconds)
