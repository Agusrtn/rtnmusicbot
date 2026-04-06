import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Configuración del bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Configuración de yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': False,
    'no_warnings': False,
}

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
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
        
        if 'entries' in data:
            data = data['entries'][0]
        
        filename = data['url']
        return cls(discord.FFmpegPCMAudio(filename, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", options="-vn"), data=data)

@bot.event
async def on_ready():
    print(f'{bot.user} se ha conectado a Discord!')

@bot.tree.command(name="play", description="Reproduce música de YouTube")
async def play(interaction: discord.Interaction, cancion: str):
    """
    Reproduce una canción de YouTube en el canal de voz
    """
    # Verificar si el usuario está en un canal de voz
    if not interaction.user.voice:
        await interaction.response.send_message("❌ Debes estar en un canal de voz para usar este comando.", ephemeral=True)
        return
    
    channel = interaction.user.voice.channel
    
    # Defer la respuesta ya que puede tomar tiempo
    await interaction.response.defer()
    
    try:
        # Conectar al canal de voz
        voice_client = await channel.connect()
    except discord.ClientException:
        voice_client = interaction.guild.voice_client
    
    try:
        await interaction.followup.send(f"🎵 Buscando: **{cancion}**...")
        
        # Descargar la información de la canción
        source = await YTDLSource.from_url(cancion, loop=bot.loop)
        
        # Reproducir la canción
        voice_client.play(source, after=lambda e: print(f'Error: {e}') if e else None)
        
        await interaction.followup.send(f"▶️ Reproduciendo: **{source.title}**")
    
    except Exception as e:
        await interaction.followup.send(f"❌ Error al reproducir la canción: {str(e)}")

@bot.tree.command(name="stop", description="Detiene la reproducción de música")
async def stop(interaction: discord.Interaction):
    """
    Detiene la reproducción y desconecta del canal de voz
    """
    if not interaction.guild.voice_client:
        await interaction.response.send_message("❌ El bot no está en un canal de voz.", ephemeral=True)
        return
    
    voice_client = interaction.guild.voice_client
    voice_client.stop()
    await voice_client.disconnect()
    await interaction.response.send_message("⏹️ Música detenida y bot desconectado.")

@bot.tree.command(name="pause", description="Pausa la música")
async def pause(interaction: discord.Interaction):
    """
    Pausa la reproducción de música
    """
    if not interaction.guild.voice_client:
        await interaction.response.send_message("❌ El bot no está reproduciendo nada.", ephemeral=True)
        return
    
    voice_client = interaction.guild.voice_client
    if voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("⏸️ Música pausada.")
    else:
        await interaction.response.send_message("❌ El bot no está reproduciendo nada.", ephemeral=True)

@bot.tree.command(name="resume", description="Reanuda la música")
async def resume(interaction: discord.Interaction):
    """
    Reanuda la reproducción de música
    """
    if not interaction.guild.voice_client:
        await interaction.response.send_message("❌ El bot no está en un canal de voz.", ephemeral=True)
        return
    
    voice_client = interaction.guild.voice_client
    if voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("▶️ Música reanudada.")
    else:
        await interaction.response.send_message("❌ La música no está pausada.", ephemeral=True)

@bot.tree.command(name="help", description="Muestra los comandos disponibles")
async def help_command(interaction: discord.Interaction):
    """
    Muestra la lista de comandos disponibles
    """
    embed = discord.Embed(title="🎵 Comandos Disponibles", color=discord.Color.blue())
    embed.add_field(name="/play <canción>", value="Reproduce una canción de YouTube", inline=False)
    embed.add_field(name="/pause", value="Pausa la música actual", inline=False)
    embed.add_field(name="/resume", value="Reanuda la música pausada", inline=False)
    embed.add_field(name="/stop", value="Detiene la música y desconecta del canal", inline=False)
    embed.add_field(name="/help", value="Muestra este mensaje de ayuda", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Ejecutar el bot
if __name__ == "__main__":
    bot.run(TOKEN)
