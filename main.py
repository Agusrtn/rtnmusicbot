import discord
import yt_dlp as youtube_dl
import os
from dotenv import load_dotenv
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO)


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
bot = discord.Bot(intents=intents)

# Configuración de yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'ignoreerrors': False,
    'source_address': '0.0.0.0',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
        }
    },
}

if COOKIES_FILE:
    ytdl_format_options['cookiefile'] = COOKIES_FILE

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None):
        loop = loop or bot.loop
        format_candidates = [
            'bestaudio[acodec!=none]/bestaudio/best',
            'bestaudio/best',
            'bestaudio*',
            'best[ext=mp4]/best',
            'best',
        ]
        last_error = None

        for fmt in format_candidates:
            try:
                local_options = dict(ytdl_format_options)
                local_options['format'] = fmt
                if COOKIES_FILE:
                    local_options['cookiefile'] = COOKIES_FILE

                local_ytdl = youtube_dl.YoutubeDL(local_options)
                data = await loop.run_in_executor(None, lambda: local_ytdl.extract_info(url, download=False))

                if not data:
                    continue

                if 'entries' in data:
                    data = next((entry for entry in data['entries'] if entry), None)
                    if not data:
                        continue

                filename = data.get('url')
                if not filename:
                    continue

                logging.info(f"✅ Formato seleccionado: {fmt}")
                return cls(
                    discord.FFmpegPCMAudio(
                        filename,
                        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        options="-vn",
                    ),
                    data=data,
                )
            except Exception as err:
                last_error = err

        # Ultimo fallback: extraer metadata sin forzar formato y elegir audio manualmente.
        try:
            fallback_options = dict(ytdl_format_options)
            fallback_options.pop('format', None)
            if COOKIES_FILE:
                fallback_options['cookiefile'] = COOKIES_FILE
            local_ytdl = youtube_dl.YoutubeDL(fallback_options)
            data = await loop.run_in_executor(None, lambda: local_ytdl.extract_info(url, download=False))

            if 'entries' in data:
                data = next((entry for entry in data['entries'] if entry), None)

            if not data:
                raise RuntimeError("No se obtuvieron datos de YouTube")

            formats = data.get('formats') or []
            audio_formats = [
                f for f in formats
                if f.get('url') and f.get('acodec') not in (None, 'none')
            ]

            if not audio_formats:
                raise RuntimeError("No hay formatos de audio disponibles en este video")

            # Prioriza audio-only por bitrate; si no existe, usa el mejor con audio.
            audio_only = [f for f in audio_formats if f.get('vcodec') in (None, 'none')]
            candidates = audio_only if audio_only else audio_formats
            best = max(candidates, key=lambda f: (f.get('abr') or 0, f.get('tbr') or 0))
            filename = best.get('url')

            if not filename:
                raise RuntimeError("El formato seleccionado no incluye URL")

            logging.info("✅ Formato seleccionado por fallback manual")
            return cls(
                discord.FFmpegPCMAudio(
                    filename,
                    before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    options="-vn",
                ),
                data=data,
            )
        except Exception as err:
            last_error = err

        raise RuntimeError(f"No se pudo extraer audio con formatos alternativos: {last_error}")

@bot.event
async def on_ready():
    print(f'✅ {bot.user} está listo!')


async def safe_reply(ctx: discord.ApplicationContext, content: str, ephemeral: bool = False):
    try:
        if not getattr(ctx, "responded", False):
            await ctx.respond(content, ephemeral=ephemeral)
        else:
            await ctx.send(content)
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
    # Responder rapido para evitar timeout de interaccion.
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
        # Descargar la información de la canción
        source = await YTDLSource.from_url(cancion, loop=bot.loop)
        
        # Reproducir la canción
        voice_client.play(source, after=lambda e: print(f'Error: {e}') if e else None)

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
    embed.add_field(name="/help", value="Muestra este mensaje de ayuda", inline=False)
    
    await ctx.respond(embed=embed, ephemeral=True)

# Ejecutar el bot
if __name__ == "__main__":
    start_healthcheck_server()

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
