import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.logger import setup_logger

logger = setup_logger(__name__)

DEFAULT_MOD_ROLE_IDS = [
	1185158470958333953
]

STATUS_EMOJI = {
	discord.Status.online: "🟢",
	discord.Status.idle: "🟡",
	discord.Status.dnd: "🔴",
}


class Call(commands.Cog):
	def __init__(self, bot: commands.Bot):
		self.bot = bot

	def _is_mod(self, member: discord.Member) -> bool:
		mod_role_ids = getattr(config, "MOD_ROLE_IDS", DEFAULT_MOD_ROLE_IDS)
		return any(role.id in mod_role_ids for role in member.roles)

	def _get_active_mods(self, guild: discord.Guild) -> list[discord.Member]:
		active_statuses = {discord.Status.online, discord.Status.idle, discord.Status.dnd}
		return [
			member
			for member in guild.members
			if not member.bot and self._is_mod(member) and member.status in active_statuses
		]

	@commands.command(name="call")
	async def call(self, ctx: commands.Context, code: str = ""):
		"""Gọi hỗ trợ khẩn cấp: !call 911"""
		allowed_channels = getattr(config, "CALL_ALLOWED_CHANNELS", [])
		if allowed_channels and ctx.channel.id not in allowed_channels:
			await ctx.send("❌ Lệnh này chỉ dùng được ở kênh được chỉ định.")
			return

		if code != "911":
			await ctx.send("📞 Dùng lệnh: `!call 911`")
			return

		if ctx.guild is None:
			await ctx.send("❌ Lệnh này chỉ dùng được trong server.")
			return

		active_mods = self._get_active_mods(ctx.guild)

		if not active_mods:
			embed = discord.Embed(
				title="🚨 CALL 911",
				description="Hiện chưa có mod nào đang hoạt động (online/idle/dnd).",
				color=discord.Color.orange(),
			)
			await ctx.send(embed=embed)
			return

		lines = []
		mentions = []
		for member in active_mods[:15]:
			status_emoji = STATUS_EMOJI.get(member.status, "⚪")
			voice_text = f" • 🔊 {member.voice.channel.mention}" if member.voice and member.voice.channel else ""
			lines.append(f"{status_emoji} {member.mention}{voice_text}")
			mentions.append(member.mention)

		extra_count = len(active_mods) - len(lines)
		if extra_count > 0:
			lines.append(f"… và **{extra_count}** mod khác đang hoạt động")

		embed = discord.Embed(
			title="🚨 CALL 911 - Mod đang hoạt động",
			description="\n".join(lines),
			color=discord.Color.red(),
		)
		embed.set_footer(text=f"Tổng cộng: {len(active_mods)} mod đang hoạt động")

		await ctx.send(content=" ".join(mentions[:10]), embed=embed)
		logger.info(
			"[CALL 911] %s gọi hỗ trợ tại #%s | active_mods=%s",
			ctx.author,
			ctx.channel,
			len(active_mods),
		)

	@app_commands.command(name="call", description="Gọi hỗ trợ khẩn cấp - Xem danh sách mod đang hoạt động")
	@app_commands.describe(code="Nhập mã 911 để gọi hỗ trợ")
	async def slash_call(self, interaction: discord.Interaction, code: str = ""):
		if interaction.guild is None:
			await interaction.response.send_message(
				"❌ Lệnh này chỉ dùng được trong server.", ephemeral=True
			)
			return

		allowed_channels = getattr(config, "CALL_ALLOWED_CHANNELS", [])
		if allowed_channels and interaction.channel_id not in allowed_channels:
			await interaction.response.send_message(
				"❌ Lệnh này chỉ dùng được ở kênh được chỉ định.", ephemeral=True
			)
			return

		if code != "911":
			await interaction.response.send_message(
				"📞 Dùng lệnh: `/call code:911`", ephemeral=True
			)
			return

		active_mods = self._get_active_mods(interaction.guild)

		if not active_mods:
			embed = discord.Embed(
				title="🚨 CALL 911",
				description="Hiện chưa có mod nào đang hoạt động (online/idle/dnd).",
				color=discord.Color.orange(),
			)
			await interaction.response.send_message(embed=embed, ephemeral=True)
			return

		lines = []
		for member in active_mods[:15]:
			status_emoji = STATUS_EMOJI.get(member.status, "⚪")
			voice_text = f" • 🔊 {member.voice.channel.mention}" if member.voice and member.voice.channel else ""
			lines.append(f"{status_emoji} {member.mention}{voice_text}")

		extra_count = len(active_mods) - len(lines)
		if extra_count > 0:
			lines.append(f"… và **{extra_count}** mod khác đang hoạt động")

		embed = discord.Embed(
			title="🚨 CALL 911 - Mod đang hoạt động.",
			description="\n".join(lines),
			color=discord.Color.red(),
		)
		embed.set_footer(text=f"Tổng cộng: {len(active_mods)} mod đang hoạt động")

		await interaction.response.send_message(embed=embed, ephemeral=True)
		logger.info(
			"[CALL 911] %s gọi hỗ trợ tại #%s | active_mods=%s",
			interaction.user,
			interaction.channel,
			len(active_mods),
		)


async def setup(bot: commands.Bot):
	await bot.add_cog(Call(bot))
