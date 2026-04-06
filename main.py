import discord
import yt_dlp as youtube_dl
import os
from dotenv import load_dotenv
import logging
import threading
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

        raise RuntimeError(f"No se pudo extraer audio con formatos alternativos: {last_error}")

@bot.event
async def on_ready():
    print(f'✅ {bot.user} está listo!')

@bot.slash_command(name="play", description="Reproduce música de YouTube")
async def play(ctx: discord.ApplicationContext, cancion: str):
    """
    Reproduce una canción de YouTube en el canal de voz
    """
    # Verificar si el usuario está en un canal de voz
    if not ctx.author.voice:
        await ctx.respond("❌ Debes estar en un canal de voz para usar este comando.", ephemeral=True)
        return
    
    channel = ctx.author.voice.channel
    
    # Defer la respuesta ya que puede tomar tiempo
    await ctx.defer()
    
    try:
        # Conectar al canal de voz
        voice_client = await channel.connect()
    except discord.ClientException:
        voice_client = ctx.guild.voice_client
    
    try:
        await ctx.followup.send(f"🎵 Buscando: **{cancion}**...")
        
        # Descargar la información de la canción
        source = await YTDLSource.from_url(cancion, loop=bot.loop)
        
        # Reproducir la canción
        voice_client.play(source, after=lambda e: print(f'Error: {e}') if e else None)
        
        await ctx.followup.send(f"▶️ Reproduciendo: **{source.title}**")
    
    except Exception as e:
        await ctx.followup.send(f"❌ Error al reproducir la canción: {str(e)}")

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
    bot.run(TOKEN)
