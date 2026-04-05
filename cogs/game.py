from discord.ext import commands
from utils.helpers import is_allowed_channel
import config
import discord
import json
import random
import asyncio
from pathlib import Path
import time

# ID kênh được phép dùng !superquiz
SUPERQUIZ_CHANNEL_ID = [1490137118859591762]  # Thay bằng ID kênh thực tế của bạn

class Game(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_quizzes = {}  # Lưu trữ quiz đang hoạt động {channel_id: quiz_data}
        self.active_songs = {}  # Lưu trữ game hát đang hoạt động {channel_id: song_data}
        # File quiz chính
        self.quiz_file = Path(__file__).parent.parent / 'data' / 'quiz_questions.json'
        self.leaderboard_file = Path(__file__).parent.parent / 'data' / 'leaderboard.json'
        self.songs_file = Path(__file__).parent.parent / 'data' / 'songs.json'
        self.questions = []
        self.leaderboard = {}
        self.songs = []
        self.used_questions_file = Path(__file__).parent.parent / 'data' / 'used_questions.json'
        self.used_questions = set()  # Lưu index của câu hỏi đã dùng
        self.correct_answers = set()  # Lưu index của câu hỏi đã trả lời đúng
        self.superquiz_channel = None  # Kênh superquiz đang hoạt động
        self.superquiz_active = False  # Trạng thái superquiz
        self.superquiz_message_count = 0  # Đếm số tin nhắn kể từ câu hỏi cuối
        self.skip_enabled = False  # Cho phép skip sau 20 tin nhắn
        self.timer_active = False  # Trạng thái hẹn giờ tự động
        self.timer_task = None  # Task của timer
        self.stoptime_votes = set()  # Lưu user_id đã vote stoptime
        self._game_startup_announce_sent = False
        self.load_questions()
        self.load_leaderboard()
        self.load_used_questions()
        self.load_songs()

    @commands.Cog.listener()
    async def on_ready(self):
        """Một lần sau khi bot kết nối: gửi embed vào (các) kênh được dùng `!superquiz` — SUPERQUIZ_CHANNEL_ID."""
        if self._game_startup_announce_sent:
            return
        self._game_startup_announce_sent = True

        cmd_lines = []
        for cmd in sorted(self.get_commands(), key=lambda c: c.name):
            if cmd.aliases:
                als = ", ".join(f"`!{a}`" for a in sorted(cmd.aliases))
                cmd_lines.append(f"`!{cmd.name}` (alias: {als})")
            else:
                cmd_lines.append(f"`!{cmd.name}`")
        commands_block = "\n".join(cmd_lines)

        if config.ALLOWED_CHANNELS:
            allowed_txt = " ".join(f"<#{cid}>" for cid in config.ALLOWED_CHANNELS)
            channels_part = f"**Kênh được dùng lệnh game** "
        else:
            channels_part = "**Kênh lệnh game:** mọi kênh — `ALLOWED_CHANNELS` đang để trống."

        sq_txt = " ".join(f"<#{cid}>" for cid in SUPERQUIZ_CHANNEL_ID)
        sq_part = f"**Kênh được `!superquiz`:**\n{sq_txt}"

        description = f"{channels_part}\n\n{sq_part}\n\n**Lệnh cog game:**\n{commands_block}"
        if len(description) > 4096:
            description = description[:4093] + "..."

        embed = discord.Embed(
            title="Game cog — bot đã sẵn sàng",
            description=description,
            color=discord.Color.blurple(),
        )

        for cid in SUPERQUIZ_CHANNEL_ID:
            ch = self.bot.get_channel(cid)
            if ch is None or not isinstance(ch, discord.abc.Messageable):
                continue
            guild = getattr(ch, "guild", None)
            if guild is not None and guild.me and hasattr(ch, "permissions_for"):
                if not ch.permissions_for(guild.me).send_messages:
                    continue
            try:
                await ch.send(embed=embed)
            except discord.HTTPException:
                continue
    
    def load_leaderboard(self):
        """Load bảng xếp hạng từ file JSON"""
        try:
            if self.leaderboard_file.exists():
                with open(self.leaderboard_file, 'r', encoding='utf-8') as f:
                    self.leaderboard = json.load(f)
            else:
                self.leaderboard = {}
            print(f"🏆 Đã load bảng xếp hạng với {len(self.leaderboard)} người chơi")
        except Exception as e:
            print(f"❌ Lỗi khi load bảng xếp hạng: {e}")
            self.leaderboard = {}
    
    def load_songs(self):
        """Load danh sách bài hát từ file JSON"""
        try:
            if self.songs_file.exists():
                with open(self.songs_file, 'r', encoding='utf-8') as f:
                    self.songs = json.load(f)
            else:
                self.songs = []
            print(f"🎵 Đã load {len(self.songs)} bài hát")
        except Exception as e:
            print(f"❌ Lỗi khi load bài hát: {e}")
            self.songs = []
    
    def save_leaderboard(self):
        """Lưu bảng xếp hạng vào file JSON"""
        try:
            with open(self.leaderboard_file, 'w', encoding='utf-8') as f:
                json.dump(self.leaderboard, f, ensure_ascii=False, indent=2)
            print("💾 Đã lưu bảng xếp hạng")
        except Exception as e:
            print(f"❌ Lỗi khi lưu bảng xếp hạng: {e}")
    
    def add_kho_ga(self, user_id: str, username: str):
        """Thêm 1 khô gà cho người chơi"""
        if user_id not in self.leaderboard:
            self.leaderboard[user_id] = {
                'username': username,
                'kho_ga': 0
            }
        self.leaderboard[user_id]['username'] = username  # Cập nhật tên mới nhất
        self.leaderboard[user_id]['kho_ga'] += 1
        self.save_leaderboard()
        return self.leaderboard[user_id]['kho_ga']
    
    def get_user_kho_ga(self, user_id: str):
        """Lấy số khô gà của người chơi"""
        if user_id in self.leaderboard:
            return self.leaderboard[user_id]['kho_ga']
        return 0
    
    def clear_cache_and_reload(self):
        """Xóa cache và reload file JSON"""
        # Xóa cached questions
        self.questions = []
        
        # Force reload file từ disk (bỏ qua bất kỳ cache nào)
        self.load_questions()
        print("✅ Đã xóa cache và reload questions từ file JSON")
    
    def load_questions(self):
        """Load câu hỏi từ file quiz_questions.json"""
        self.questions = []
        try:
            if self.quiz_file.exists():
                with open(self.quiz_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.questions = data
                        print(f"✅ Đã load {len(self.questions)} câu hỏi từ quiz_questions.json")
                    else:
                        print(f"❌ Format file không đúng, cần là một list")
            else:
                print(f"⚠️ File không tồn tại: {self.quiz_file}")
        except Exception as e:
            print(f"❌ Lỗi khi load câu hỏi quiz: {e}")
            self.questions = []
    
    def load_used_questions(self):
        """Load danh sách câu hỏi đã sử dụng"""
        try:
            if self.used_questions_file.exists():
                with open(self.used_questions_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.used_questions = set(data.get('used_indices', []))
                    self.correct_answers = set(data.get('correct_indices', []))
            else:
                self.used_questions = set()
                self.correct_answers = set()
            print(f"📝 Đã sử dụng {len(self.used_questions)}/{len(self.questions)} câu hỏi")
            print(f"✅ Trả lời đúng {len(self.correct_answers)}/{len(self.used_questions)} câu hỏi")
        except Exception as e:
            print(f"❌ Lỗi khi load used questions: {e}")
            self.used_questions = set()
            self.correct_answers = set()
    
    def save_used_questions(self):
        """Lưu danh sách câu hỏi đã sử dụng"""
        try:
            with open(self.used_questions_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'used_indices': list(self.used_questions),
                    'correct_indices': list(self.correct_answers)
                }, f, ensure_ascii=False, indent=2)
            print(f"💾 Đã lưu tiến trình: {len(self.used_questions)}/{len(self.questions)} câu hỏi")
            print(f"✅ Đã lưu {len(self.correct_answers)} câu trả lời đúng")
        except Exception as e:
            print(f"❌ Lỗi khi lưu used questions: {e}")
    
    def reset_quiz_progress(self):
        """Reset tiến trình quiz về đầu"""
        self.used_questions = set()
        self.correct_answers = set()
        self.save_used_questions()
        print("🔄 Đã reset tiến trình quiz")
    
    @commands.command(name='quiz')
    @is_allowed_channel()
    async def quiz(self, ctx):
        """Bắt đầu một câu hỏi quiz. Trả lời đúng nhận khô gà!"""
        
        # Xóa cache và load lại câu hỏi mỗi lần chạy để đảm bảo dữ liệu mới nhất
        self.clear_cache_and_reload()
        self.load_used_questions()
        
        # Nếu có superquiz đang chạy và lệnh từ kênh khác
        if self.superquiz_active and self.superquiz_channel and ctx.channel.id != self.superquiz_channel.id:
            # Kiểm tra xem superquiz có quiz đang chạy không
            if self.superquiz_channel.id in self.active_quizzes:
                sq_quiz = self.active_quizzes[self.superquiz_channel.id]
                question_index = sq_quiz['question_index']
                
                # Hiển thị cùng câu hỏi ở kênh này
                embed = discord.Embed(
                    title=f"🎯 Quiz Time! - Câu #{question_index + 1}",
                    description=sq_quiz['question_text'],
                    color=discord.Color.blue()
                )
                
                remaining = len(self.questions) - len(self.used_questions)
                current_position = len(self.used_questions) + 1
                embed.set_footer(text=f"Bạn có 20 giây để trả lời! | Tiến độ: {current_position}/{len(self.questions)} | Dùng !superquiz để tự động")
                
                quiz_message = await ctx.send(embed=embed)
                
                # Lưu thông tin quiz cho kênh này (cùng câu hỏi với superquiz)
                self.active_quizzes[ctx.channel.id] = {
                    'quiz_id': time.time(),
                    'question_index': question_index,
                    'question_text': sq_quiz['question_text'],
                    'answer': sq_quiz['answer'],
                    'alternatives': sq_quiz['alternatives'],
                    'message': quiz_message,
                    'answered': False,
                    'is_superquiz': False,
                    'linked_to_superquiz': True  # Đánh dấu là quiz liên kết với superquiz
                }
                
                # Thêm timeout cho quiz này
                quiz_id = self.active_quizzes[ctx.channel.id]['quiz_id']
                for _ in range(7):
                    await asyncio.sleep(1)
                    # Kiểm tra xem quiz đã bị xóa hoặc đã được trả lời chưa
                    if ctx.channel.id not in self.active_quizzes:
                        return  # Quiz đã được trả lời và xóa
                    if self.active_quizzes[ctx.channel.id].get('quiz_id') != quiz_id:
                        return  # Có quiz mới thay thế
                    if self.active_quizzes[ctx.channel.id].get('answered'):
                        return  # Đã được trả lời
                
                # Kiểm tra lần cuối xem quiz có còn tồn tại và chưa được trả lời
                if ctx.channel.id in self.active_quizzes:
                    saved_quiz = self.active_quizzes.get(ctx.channel.id)
                    if saved_quiz and saved_quiz.get('quiz_id') == quiz_id and not saved_quiz.get('answered'):
                        correct_answer = saved_quiz['answer']
                        question_index = saved_quiz['question_index']
                        
                        # Đánh dấu câu hỏi đã sử dụng
                        self.used_questions.add(question_index)
                        self.save_used_questions()
                        
                        # Tính số câu hỏi còn lại
                        remaining = len(self.questions) - len(self.used_questions)
                        
                        embed = discord.Embed(
                            title=f"⏰ Hết giờ! - Câu #{question_index + 1}",
                            description=f"Không có ai trả lời đúng.\n**Đáp án:** {correct_answer}",
                            color=discord.Color.red()
                        )
                        embed.set_footer(text=f"Còn lại {remaining} câu hỏi chưa trả lời. Dùng !quiz để tiếp tục!")
                        await ctx.send(embed=embed)
                        
                        # Thông báo cho kênh superquiz
                        if self.superquiz_active and self.superquiz_channel:
                            if self.superquiz_channel.id in self.active_quizzes:
                                sq_quiz_check = self.active_quizzes[self.superquiz_channel.id]
                                if sq_quiz_check['question_index'] == question_index:
                                    sq_embed = discord.Embed(
                                        title=f"⏰ Hết Giờ Ở Kênh Khác - Câu #{question_index + 1}",
                                        description=f"**Đáp án:** {correct_answer}\n\n➡️ Đang chuyển sang câu tiếp theo...",
                                        color=discord.Color.orange()
                                    )
                                    await self.superquiz_channel.send(embed=sq_embed)
                                    
                                    # Xóa và chuyển câu mới cho superquiz
                                    del self.active_quizzes[self.superquiz_channel.id]
                                    await asyncio.sleep(2)
                                    await self.start_quiz(self.superquiz_channel, is_superquiz=True)
                        
                        # Xóa quiz khỏi active list
                        if ctx.channel.id in self.active_quizzes:
                            del self.active_quizzes[ctx.channel.id]
                
                return
        
        # Kiểm tra xem channel này đã có quiz đang chạy chưa
        if ctx.channel.id in self.active_quizzes:
            # Lấy thông tin câu hỏi hiện tại
            current_quiz = self.active_quizzes[ctx.channel.id]
            question_index = current_quiz['question_index']
            question_text = current_quiz['question_text']
            
            # Nhắc lại câu hỏi
            embed = discord.Embed(
                title=f"⚠️ Câu Hỏi Chưa Được Trả Lời! - Câu #{question_index + 1}",
                description=question_text,
                color=discord.Color.orange()
            )
            embed.set_footer(text="Hãy trả lời câu hỏi này trước khi bắt đầu câu mới!")
            await ctx.send(embed=embed)
            return
        
        # Gọi hàm start_quiz chung
        await self.start_quiz(ctx.channel)
    
    async def start_quiz(self, channel, is_superquiz=False):
        """Hàm chung để bắt đầu quiz ở một channel"""
        # Kiểm tra có câu hỏi không
        if not self.questions:
            await channel.send("❌ Không có câu hỏi nào trong database!")
            return False
        
        # Kiểm tra xem đã hết câu hỏi chưa
        if len(self.used_questions) >= len(self.questions):
            embed = discord.Embed(
                title="🎊 Hoàn thành!",
                description=f"Đã hết câu hỏi! Bạn đã trả lời hết **{len(self.questions)}** câu hỏi.\n\nSử dụng `!resetquiz` để bắt đầu lại từ đầu.",
                color=discord.Color.gold()
            )
            embed.set_footer(text="Chúc mừng bạn đã hoàn thành tất cả câu hỏi!")
            await channel.send(embed=embed)
            
            # Tắt superquiz nếu đang chạy
            if is_superquiz:
                self.superquiz_active = False
                self.superquiz_channel = None
            return False
        
        # Tìm câu hỏi tiếp theo chưa sử dụng (theo thứ tự)
        question_index = None
        for i in range(len(self.questions)):
            if i not in self.used_questions:
                question_index = i
                break
        
        if question_index is None:
            await channel.send("❌ Lỗi: Không tìm thấy câu hỏi khả dụng!")
            return False
        
        question_data = self.questions[question_index].copy()
        
        # Tạo unique ID cho quiz này
        quiz_id = time.time()
        
        # Debug: In ra câu hỏi và đáp án
        print(f"🎯 Câu hỏi: {question_data['question']}")
        print(f"✅ Đáp án đúng: {question_data['answer']}")
        
        # Tạo embed cho câu hỏi
        remaining = len(self.questions) - len(self.used_questions)
        current_position = len(self.used_questions) + 1
        
        title = f"🎯 Quiz Time! - Câu #{question_index + 1}"
        if is_superquiz:
            title = f"⚡ Super Quiz! - Câu #{question_index + 1}"
        
        embed = discord.Embed(
            title=title,
            description=question_data['question'],
            color=discord.Color.blue() if not is_superquiz else discord.Color.purple()
        )
        
        footer_text = f"Bạn có 15  giây để trả lời! | Tiến độ: {current_position}/{len(self.questions)} câu hỏi"
        if is_superquiz:
            footer_text = f"Super Quiz đang chạy! | Tiến độ: {current_position}/{len(self.questions)} câu hỏi"
        else:
            footer_text = f"Bạn có 15 giây để trả lời! | Tiến độ: {current_position}/{len(self.questions)} | Dùng !superquiz để tự động"
        
        embed.set_footer(text=footer_text)
        
        quiz_message = await channel.send(embed=embed)
        
        # Lưu thông tin quiz - lưu trực tiếp question và answer
        self.active_quizzes[channel.id] = {
            'quiz_id': quiz_id,
            'question_index': question_index,
            'question_text': question_data['question'],
            'answer': question_data['answer'],
            'alternatives': question_data.get('alternatives', []),
            'message': quiz_message,
            'answered': False,
            'is_superquiz': is_superquiz
        }
        
        # Bắt đầu timer nếu là superquiz và timer đang bật
        if is_superquiz and self.timer_active:
            await self.start_question_timer(channel)
        
        # Đợi 15giây - chỉ timeout cho quiz thường, superquiz sẽ không timeout
        if not is_superquiz:
            for _ in range(15):
                await asyncio.sleep(1)
                # Kiểm tra xem quiz đã bị xóa hoặc đã được trả lời chưa
                if channel.id not in self.active_quizzes:
                    return True  # Quiz đã được trả lời và xóa
                if self.active_quizzes[channel.id].get('quiz_id') != quiz_id:
                    return True  # Có quiz mới thay thế
                if self.active_quizzes[channel.id].get('answered'):
                    return True  # Đã được trả lời
            
            # Kiểm tra lần cuối xem quiz có còn tồn tại và chưa được trả lời
            if channel.id in self.active_quizzes:
                saved_quiz = self.active_quizzes.get(channel.id)
                # Đảm bảo đúng quiz (cùng quiz_id) và chưa được trả lời
                if saved_quiz and saved_quiz.get('quiz_id') == quiz_id and not saved_quiz.get('answered'):
                    correct_answer = saved_quiz['answer']
                    question_index = saved_quiz['question_index']
                    
                    # Đánh dấu câu hỏi đã sử dụng để chuyển sang câu kế tiếp
                    self.used_questions.add(question_index)
                    self.save_used_questions()
                    
                    # Tính số câu hỏi còn lại
                    remaining = len(self.questions) - len(self.used_questions)
                    
                    embed = discord.Embed(
                        title=f"⏰ Hết giờ! - Câu #{question_index + 1}",
                        description=f"Không có ai trả lời đúng.\n**Đáp án:** {correct_answer}",
                        color=discord.Color.red()
                    )
                    embed.set_footer(text=f"Còn lại {remaining} câu hỏi chưa trả lời. Dùng !quiz để tiếp tục!")
                    await channel.send(embed=embed)
                    
                    # Thông báo cho kênh superquiz nếu timeout từ kênh khác
                    if self.superquiz_active and self.superquiz_channel and channel.id != self.superquiz_channel.id:
                        # Kiểm tra xem có quiz cùng câu hỏi ở superquiz không
                        if self.superquiz_channel.id in self.active_quizzes:
                            sq_quiz = self.active_quizzes[self.superquiz_channel.id]
                            if sq_quiz['question_index'] == question_index:
                                # Gửi thông báo đáp án cho kênh superquiz
                                sq_embed = discord.Embed(
                                    title=f"⏰ Hết Giờ Ở Kênh Khác - Câu #{question_index + 1}",
                                    description=f"**Đáp án:** {correct_answer}\n\n➡️ Đang chuyển sang câu tiếp theo...",
                                    color=discord.Color.orange()
                                )
                                await self.superquiz_channel.send(embed=sq_embed)
                    
                    # Xóa quiz khỏi active list
                    if channel.id in self.active_quizzes:
                        del self.active_quizzes[channel.id]
        
        return True
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Lắng nghe câu trả lời quiz và game hát"""
        
        # Bỏ qua tin nhắn của bot
        if message.author.bot:
            return
        
        # Đếm tin nhắn trong superquiz channel
        if self.superquiz_active and self.superquiz_channel and message.channel.id == self.superquiz_channel.id:
            if message.channel.id in self.active_quizzes:
                self.superquiz_message_count += 1
                
                # Bật skip sau 20 tin nhắn
                if self.superquiz_message_count >= 20 and not self.skip_enabled:
                    self.skip_enabled = True
                    skip_embed = discord.Embed(
                        title="⏭️ Có Thể Skip",
                        description="Đã có 20 tin nhắn mà không ai trả lời đúng!\n\nBạn có thể dùng lệnh `!skip` để bỏ qua câu này.",
                        color=discord.Color.orange()
                    )
                    await message.channel.send(embed=skip_embed)
        
        # Kiểm tra game hát trước
        if message.channel.id in self.active_songs:
            await self.handle_singing(message)
            return
        
        # Kiểm tra xem channel này có quiz đang chạy không
        if message.channel.id not in self.active_quizzes:
            return
        
        quiz_data = self.active_quizzes[message.channel.id]
        
        # Kiểm tra xem quiz đã được trả lời chưa
        if quiz_data.get('answered'):
            return
        
        # Normalize câu trả lời
        user_answer = message.content.lower().strip()
        correct_answer = quiz_data['answer'].lower()
        alternatives = [alt.lower() for alt in quiz_data.get('alternatives', [])]
        
        # Kiểm tra câu trả lời
        if user_answer == correct_answer or user_answer in alternatives:
            # Đánh dấu đã trả lời để tránh race condition
            self.active_quizzes[message.channel.id]['answered'] = True
            
            # Reset đếm tin nhắn, tắt skip và reset vote khi có người trả lời đúng
            if self.superquiz_active and message.channel.id == self.superquiz_channel.id:
                self.superquiz_message_count = 0
                self.skip_enabled = False
                self.stoptime_votes.clear()  # Reset vote stoptime
            
            # Đánh dấu câu hỏi đã sử dụng VÀ trả lời đúng
            question_index = quiz_data['question_index']
            self.used_questions.add(question_index)
            self.correct_answers.add(question_index)  # Thêm vào danh sách trả lời đúng
            self.save_used_questions()
            
            # Thêm khô gà cho người chơi
            user_id = str(message.author.id)
            username = message.author.display_name
            total_kho_ga = self.add_kho_ga(user_id, username)
            
            # Tính số câu hỏi còn lại
            remaining = len(self.questions) - len(self.used_questions)
            
            # Trả lời đúng!
            question_id = quiz_data['question_index'] + 1
            embed = discord.Embed(
                title=f"🎉 Chính xác! - Câu #{question_id}",
                description=f"{message.author.mention} đã trả lời đúng!\n**Đáp án:** {quiz_data['answer']}\n\n🍗 **Tổng khô gà:** {total_kho_ga} hộp",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Còn lại {remaining} câu hỏi chưa trả lời")
            embed.set_image(url="https://media.discordapp.net/attachments/1459229057706364939/1461582560332087399/shopee_vn-11134103-22120-depw1vmsf0kv16.png?ex=696b1455&is=6969c2d5&hm=eddc74afe016d7f9dd4e1922f6ab7930a0446e1e29ba21bc988cf1c817a93d58&=&format=webp&quality=lossless&width=555&height=740")
            
            reward_msg = await message.channel.send(f"{message.author.mention} nhận được **1 Hộp Khô Gà Mixi** 🥳🍗", embed=embed)
            
            # Xóa quiz khỏi active list
            del self.active_quizzes[message.channel.id]
            
            # Thu thập danh sách các kênh có quiz liên quan (cùng câu hỏi)
            linked_channels = []
            for ch_id, q_data in self.active_quizzes.items():
                if q_data['question_index'] == question_index:
                    linked_channels.append(ch_id)
            
            # Nếu trả lời đúng ở kênh superquiz và có kênh khác đã dùng !quiz
            if self.superquiz_active and self.superquiz_channel and message.channel.id == self.superquiz_channel.id and linked_channels:
                # Thông báo đáp án đúng cho các kênh đã dùng !quiz
                for ch_id in linked_channels:
                    try:
                        channel = self.bot.get_channel(ch_id)
                        if channel:
                            answer_embed = discord.Embed(
                                title=f"✅ Có Người Trả Lời Đúng - Câu #{question_id}",
                                description=f"**Đáp án:** {quiz_data['answer']}\n\nCó người đã trả lời đúng ở kênh superquiz!",
                                color=discord.Color.green()
                            )
                            await channel.send(embed=answer_embed)
                    except Exception as e:
                        print(f"❌ Lỗi khi gửi thông báo đến kênh {ch_id}: {e}")
            
            # Nếu trả lời đúng ở kênh thường và có superquiz đang chạy
            elif self.superquiz_active and self.superquiz_channel and message.channel.id != self.superquiz_channel.id:
                # Thông báo cho kênh superquiz
                sq_embed = discord.Embed(
                    title=f"✅ Đã Có Người Trả Lời Đúng - Câu #{question_id}",
                    description=f"**Đáp án:** {quiz_data['answer']}\n\n➡️ Đang chuyển sang câu tiếp theo...",
                    color=discord.Color.green()
                )
                await self.superquiz_channel.send(embed=sq_embed)
            
            # Nếu có superquiz đang chạy, xóa quiz ở tất cả các kênh liên quan
            if self.superquiz_active and self.superquiz_channel:
                # Xóa quiz ở superquiz channel nếu có
                if self.superquiz_channel.id in self.active_quizzes:
                    del self.active_quizzes[self.superquiz_channel.id]
                
                # Xóa tất cả quiz được link với superquiz
                channels_to_remove = []
                for ch_id, q_data in self.active_quizzes.items():
                    if q_data.get('linked_to_superquiz') and q_data['question_index'] == question_index:
                        channels_to_remove.append(ch_id)
                
                for ch_id in channels_to_remove:
                    del self.active_quizzes[ch_id]
            
            # Nếu là superquiz hoặc có superquiz đang chạy, tự động chạy câu tiếp theo
            if quiz_data.get('is_superquiz') or (self.superquiz_active and self.superquiz_channel):
                # Hủy timer cũ nếu có
                if self.timer_task and not self.timer_task.done():
                    self.timer_task.cancel()
                
                await asyncio.sleep(1)  # Đợi 1 giây trước khi chạy câu tiếp
                if self.superquiz_active and self.superquiz_channel:
                    await self.start_quiz(self.superquiz_channel, is_superquiz=True)

    @commands.command(name='superquiz')
    @is_allowed_channel()
    async def superquiz(self, ctx):
        """Bắt đầu chế độ Super Quiz - chạy liên tục cho đến khi dừng lại"""
        
        # Kiểm tra xem có phải kênh được phép không
        if ctx.channel.id not in SUPERQUIZ_CHANNEL_ID:
            channel_mentions = " hoặc ".join([f"<#{ch_id}>" for ch_id in SUPERQUIZ_CHANNEL_ID])
            embed = discord.Embed(
                title="❌ Kênh Không Hợp Lệ",
                description=f"Lệnh `!superquiz` chỉ có thể dùng ở kênh {channel_mentions}!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Xóa cache và load lại
        self.clear_cache_and_reload()
        self.load_used_questions()
        
        # Kiểm tra xem đã có superquiz đang chạy chưa
        if self.superquiz_active:
            embed = discord.Embed(
                title="⚠️ Super Quiz Đang Chạy",
                description=f"Super Quiz đang chạy ở {self.superquiz_channel.mention}!\n\nDùng `!stopsuperquiz` để dừng lại.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem channel này có quiz thường đang chạy không
        if ctx.channel.id in self.active_quizzes:
            embed = discord.Embed(
                title="⚠️ Có Quiz Đang Chạy",
                description="Hãy trả lời câu hỏi hiện tại trước khi bắt đầu Super Quiz!",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Bật superquiz
        self.superquiz_active = True
        self.superquiz_channel = ctx.channel
        self.superquiz_message_count = 0
        self.skip_enabled = False
        
        # Thông báo bắt đầu
        embed = discord.Embed(
            title="⚡ Super Quiz Bắt Đầu!",
            description=f"Super Quiz sẽ chạy liên tục ở kênh này!\n\n📌 Đặc điểm:\n- Câu hỏi sẽ tự động chuyển khi có người trả lời đúng\n- Không tự động nhảy câu nếu không ai trả lời\n- Sau 20 tin nhắn không có câu trả lời đúng, có thể dùng `!skip` để bỏ qua\n- Có thể nhảy câu nếu có lệnh `!quiz` từ kênh khác\n\n⏰ **Hẹn giờ tự động:**\n- Dùng `!time` để bật hẹn giờ (tự động chuyển câu sau 7 giây)\n- Dùng `!stoptime` để tắt (cần 3 người vote)\n\n🛑 Dùng `!stopsuperquiz` để dừng lại",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)
        
        # Đợi 1 giây rồi bắt đầu câu hỏi đầu tiên
        await asyncio.sleep(2)
        await self.start_quiz(ctx.channel, is_superquiz=True)
    
    @commands.command(name='stopsuperquiz')
    @is_allowed_channel()
    async def stop_superquiz(self, ctx):
        """Dừng chế độ Super Quiz"""
        
        # Kiểm tra xem có superquiz đang chạy không
        if not self.superquiz_active:
            embed = discord.Embed(
                title="⚠️ Không Có Super Quiz",
                description="Không có Super Quiz nào đang chạy!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem có phải channel đúng không
        if ctx.channel.id != self.superquiz_channel.id:
            embed = discord.Embed(
                title="⚠️ Sai Kênh",
                description=f"Super Quiz đang chạy ở {self.superquiz_channel.mention}!\n\nHãy dùng lệnh ở kênh đó để dừng.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Lấy thông tin câu hỏi hiện tại nếu có
        current_question = None
        if self.superquiz_channel.id in self.active_quizzes:
            quiz = self.active_quizzes[self.superquiz_channel.id]
            current_question = {
                'index': quiz['question_index'],
                'answer': quiz['answer']
            }
            # Xóa quiz hiện tại
            del self.active_quizzes[self.superquiz_channel.id]
        
        # Tắt timer nếu đang chạy
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()
        
        # Tắt superquiz
        self.superquiz_active = False
        channel_mention = self.superquiz_channel.mention
        self.superquiz_channel = None
        self.superquiz_message_count = 0
        self.skip_enabled = False
        self.timer_active = False
        self.timer_task = None
        self.stoptime_votes.clear()
        
        # Thông báo dừng
        embed = discord.Embed(
            title="🛑 Super Quiz Đã Dừng",
            description=f"Super Quiz ở {channel_mention} đã dừng lại!",
            color=discord.Color.red()
        )
        
        if current_question:
            embed.add_field(
                name="Câu hỏi cuối cùng",
                value=f"Câu #{current_question['index'] + 1}\nĐáp án: **{current_question['answer']}**",
                inline=False
            )
        
        embed.set_footer(text="Dùng !superquiz để bắt đầu lại")
        await ctx.send(embed=embed)

    @commands.command(name='bxh', aliases=['leaderboard', 'top', 'rank'])
    @is_allowed_channel()
    async def show_leaderboard(self, ctx):
        """Hiển thị bảng xếp hạng khô gà"""
        self.load_leaderboard()  # Reload để có dữ liệu mới nhất
        
        if not self.leaderboard:
            await ctx.send("📭 Chưa có ai trong bảng xếp hạng! Hãy chơi `!superquiz` để bắt đầu!")
            return
        
        # Sắp xếp theo số khô gà giảm dần
        sorted_players = sorted(
            self.leaderboard.items(),
            key=lambda x: x[1]['kho_ga'],
            reverse=True
        )
        
        # Tạo embed
        embed = discord.Embed(
            title="🏆 Bảng Xếp Hạng Khô Gà Mixi",
            description="Top người chơi có nhiều khô gà nhất!",
            color=discord.Color.gold()
        )
        
        # Emoji cho top 3
        medals = ['🥇', '🥈', '🥉']
        
        # Hiển thị top 10
        leaderboard_text = ""
        for i, (user_id, data) in enumerate(sorted_players[:10]):
            if i < 3:
                rank = medals[i]
            else:
                rank = f"**{i+1}.**"
            
            leaderboard_text += f"{rank} {data['username']} - **{data['kho_ga']}** 🍗\n"
        
        embed.add_field(name="📊 Top 10", value=leaderboard_text or "Chưa có dữ liệu", inline=False)
        embed.set_footer(text="Chơi !quiz để kiếm thêm khô gà!")
        embed.set_thumbnail(url="https://media.discordapp.net/attachments/1459229057706364939/1461582560332087399/shopee_vn-11134103-22120-depw1vmsf0kv16.png?ex=696b1455&is=6969c2d5&hm=eddc74afe016d7f9dd4e1922f6ab7930a0446e1e29ba21bc988cf1c817a93d58&=&format=webp&quality=lossless&width=555&height=740")
        
        leaderboard_msg = await ctx.send(embed=embed)
        
        # Xóa bảng xếp hạng sau 15 giây
        await asyncio.sleep(15)
        try:
            await leaderboard_msg.delete()
        except:
            pass
    
    @commands.command(name='khoga', aliases=['mykhoga', 'inventory', 'tuido'])
    @is_allowed_channel()
    async def my_kho_ga(self, ctx):
        """Xem số khô gà của bản thân"""
        self.load_leaderboard()
        user_id = str(ctx.author.id)
        kho_ga = self.get_user_kho_ga(user_id)
        
        # Tìm rank của người chơi
        sorted_players = sorted(
            self.leaderboard.items(),
            key=lambda x: x[1]['kho_ga'],
            reverse=True
        )
        
        rank = "Chưa có"
        for i, (uid, data) in enumerate(sorted_players):
            if uid == user_id:
                rank = f"#{i+1}"
                break
        
        embed = discord.Embed(
            title=f"🍗 Túi Khô Gà của {ctx.author.display_name}",
            color=discord.Color.orange()
        )
        embed.add_field(name="Số khô gà", value=f"**{kho_ga}** hộp 🍗", inline=True)
        embed.add_field(name="Xếp hạng", value=rank, inline=True)
        embed.set_thumbnail(url=ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url)
        embed.set_footer(text="Chơi !quiz để kiếm thêm khô gà!")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='resetquiz')
    @is_allowed_channel()
    async def reset_quiz(self, ctx):
        """Reset tiến trình quiz để bắt đầu lại từ đầu (chỉ khi đã hoàn thành 100 câu)"""
        self.load_used_questions()
        
        # Dừng superquiz nếu đang chạy
        superquiz_stopped = False
        if self.superquiz_active:
            # Xóa quiz đang chạy
            if self.superquiz_channel and self.superquiz_channel.id in self.active_quizzes:
                del self.active_quizzes[self.superquiz_channel.id]
            self.superquiz_active = False
            self.superquiz_channel = None
            superquiz_stopped = True
        
        # Kiểm tra xem đã trả lời đủ 100 câu chưa
        if len(self.used_questions) < 100:
            remaining = 100 - len(self.used_questions)
            embed = discord.Embed(
                title="⚠️ Chưa Thể Reset!",
                description=f"Bạn cần trả lời đủ **100 câu hỏi** mới có thể reset.\n\n📊 Tiến độ hiện tại: **{len(self.used_questions)}/100** câu\n🎯 Còn lại: **{remaining}** câu",
                color=discord.Color.red()
            )
            embed.set_footer(text="Hãy tiếp tục chơi !quiz để hoàn thành thử thách!")
            await ctx.send(embed=embed)
            return
        
        # Đã đủ 100 câu, cho phép reset
        self.reset_quiz_progress()
        
        description = f"Chúc mừng bạn đã hoàn thành **100 câu hỏi**! 🎉\n\nTiến trình quiz đã được reset.\n\nBây giờ bạn có thể chơi lại **{len(self.questions)}** câu hỏi từ đầu.\n\nSử dụng `!quiz` để bắt đầu!"
        
        if superquiz_stopped:
            description += "\n\n⚠️ **Super Quiz đã được dừng lại.**"
        
        embed = discord.Embed(
            title="🔄 Đã Reset Quiz!",
            description=description,
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)
    
    @commands.command(name='quizprogress', aliases=['progress', 'tiendo'])
    @is_allowed_channel()
    async def quiz_progress(self, ctx):
        """Xem tiến trình quiz hiện tại"""
        self.load_used_questions()
        
        used = len(self.used_questions)
        total = len(self.questions)
        remaining = total - used
        percentage = (used / total * 100) if total > 0 else 0
        
        embed = discord.Embed(
            title="📊 Tiến Trình Quiz",
            color=discord.Color.purple()
        )
        embed.add_field(name="Đã trả lời", value=f"**{used}** câu", inline=True)
        embed.add_field(name="Còn lại", value=f"**{remaining}** câu", inline=True)
        embed.add_field(name="Tổng cộng", value=f"**{total}** câu", inline=True)
        
        # Tạo progress bar
        bar_length = 20
        filled = int(bar_length * used / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_length - filled)
        
        embed.add_field(
            name="Tiến độ",
            value=f"{bar} {percentage:.1f}%",
            inline=False
        )
        
        if remaining == 0:
            embed.set_footer(text="🎊 Bạn đã hoàn thành tất cả câu hỏi! Dùng !resetquiz để chơi lại.")
        else:
            embed.set_footer(text="Sử dụng !quiz để tiếp tục!")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='check', aliases=['diem', 'score'])
    @is_allowed_channel()
    async def check_progress(self, ctx):
        """Kiểm tra số câu đã trả lời đúng và tỷ lệ chính xác"""
        self.load_used_questions()
        
        correct_count = len(self.correct_answers)
        used_count = len(self.used_questions)
        total_count = len(self.questions)
        
        # Tính tỷ lệ chính xác
        accuracy = (correct_count / used_count * 100) if used_count > 0 else 0
        
        embed = discord.Embed(
            title="📊 Thống Kê Quiz",
            description="Kết quả trả lời câu hỏi của tất cả mọi người",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="✅ Trả lời đúng", value=f"**{correct_count}** câu", inline=True)
        embed.add_field(name="📝 Đã sử dụng", value=f"**{used_count}** câu", inline=True)
        embed.add_field(name="📚 Tổng cộng", value=f"**{total_count}** câu", inline=True)
        
        # Tính số câu timeout (hết giờ)
        timeout_count = used_count - correct_count
        embed.add_field(name="⏰ Hết giờ", value=f"**{timeout_count}** câu", inline=True)
        embed.add_field(name="🎯 Độ chính xác", value=f"**{accuracy:.1f}%**", inline=True)
        embed.add_field(name="📌 Còn lại", value=f"**{total_count - used_count}** câu", inline=True)
        
        # Tạo progress bar cho độ chính xác
        bar_length = 20
        filled = int(bar_length * accuracy / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        embed.add_field(
            name="Tiến độ chính xác",
            value=f"{bar} {accuracy:.1f}%",
            inline=False
        )
        
        embed.set_footer(text="Dùng !quiz để tiếp tục chơi!")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='modresetquiz')
    @commands.has_permissions(administrator=True)
    async def mod_reset_quiz(self, ctx):
        """[MOD ONLY] Force reset tiến trình quiz mà không cần trả lời đủ 100 câu"""
        self.load_used_questions()
        
        # Dừng superquiz nếu đang chạy
        superquiz_stopped = False
        if self.superquiz_active:
            # Xóa quiz đang chạy
            if self.superquiz_channel and self.superquiz_channel.id in self.active_quizzes:
                del self.active_quizzes[self.superquiz_channel.id]
            self.superquiz_active = False
            self.superquiz_channel = None
            superquiz_stopped = True
        
        old_progress = len(self.used_questions)
        self.reset_quiz_progress()
        
        # Thông báo kết quả
        description = f"🔧 **[MOD]** Đã force reset tiến trình quiz!\n\n📊 Tiến độ cũ: **{old_progress}/{len(self.questions)}** câu\n✅ Tiến độ mới: **0/{len(self.questions)}** câu\n\nBây giờ có thể chơi lại từ đầu!"
        
        if superquiz_stopped:
            description += "\n\n⚠️ **Super Quiz đã được dừng lại.**"
        
        embed = discord.Embed(
            title="🔄 Force Reset Quiz!",
            description=description,
            color=discord.Color.gold()
        )
        embed.set_footer(text="Sử dụng !quiz để bắt đầu!")
        await ctx.send(embed=embed)
        
    @mod_reset_quiz.error
    async def mod_reset_quiz_error(self, ctx, error):
        """Xử lý lỗi khi không có quyền"""
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="❌ Không Có Quyền",
                description="Bạn cần quyền **Administrator** để sử dụng lệnh này!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
    
    @commands.command(name='sing', aliases=['hat'])
    async def sing(self, ctx):
        """Hát cùng bot! Bot sẽ cho bạn dòng nhạc đầu tiên, bạn hát tiếp theo"""
        
        # Kiểm tra có bài hát không
        if not self.songs:
            await ctx.send("❌ Không có bài hát nào trong database!")
            return
        
        # Kiểm tra xem channel này đã có game hát đang chạy chưa
        if ctx.channel.id in self.active_songs:
            await ctx.send("⚠️ Đã có game hát đang chạy trong kênh này! Hãy hoàn thành bài hát hiện tại trước.")
            return
        
        # Chọn ngẫu nhiên một bài hát
        song = random.choice(self.songs)
        
        # Bắt đầu từ dòng đầu tiên
        current_line_index = 0
        
        # Lưu trạng thái game hát
        self.active_songs[ctx.channel.id] = {
            'song': song,
            'current_line_index': current_line_index,
            'started_by': ctx.author.id
        }
        
        # Tạo embed
        embed = discord.Embed(
            title="🎤 Hát Cùng Bot!",
            description=f"**{song['title']}**\n*{song['artist']}*",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="🎵 Dòng nhạc đầu tiên:",
            value=f"*\"{song['lines'][current_line_index]}\"*",
            inline=False
        )
        
        embed.set_footer(text=f"Hãy hát dòng tiếp theo! ({current_line_index + 1}/{len(song['lines'])})")
        embed.set_thumbnail(url="https://media.discordapp.net/attachments/1459229057706364939/1461582560332087399/shopee_vn-11134103-22120-depw1vmsf0kv16.png?ex=696b1455&is=6969c2d5&hm=eddc74afe016d7f9dd4e1922f6ab7930a0446e1e29ba21bc988cf1c817a93d58&=&format=webp&quality=lossless&width=555&height=740")
        
        await ctx.send(embed=embed)
        
        # Thông báo động viên
        encouragement = f"Vỗ tay nào! {ctx.author.mention} sẽ hát! 👏"
        await ctx.send(encouragement)
    
    async def handle_singing(self, message):
        """Xử lý tin nhắn trong game hát"""
        song_data = self.active_songs[message.channel.id]
        song = song_data['song']
        current_index = song_data['current_line_index']
        
        # Dòng tiếp theo mà người dùng cần hát
        next_index = current_index + 1
        
        # Kiểm tra xem đã hết bài hát chưa
        if next_index >= len(song['lines']):
            embed = discord.Embed(
                title="🎊 Hoàn Thành!",
                description=f"**{song['title']}** - *{song['artist']}*\n\n{message.author.mention} đã hoàn thành bài hát! 🎉",
                color=discord.Color.gold()
            )
            embed.set_footer(text="Dùng !sing để hát bài khác!")
            await message.channel.send(embed=embed)
            
            # Xóa game hát
            del self.active_songs[message.channel.id]
            return
        
        # Normalize câu trả lời
        user_input = message.content.strip().lower()
        correct_line = song['lines'][next_index].strip().lower()
        
        # Kiểm tra tương đồng (cho phép sai sót nhỏ)
        # So sánh độ tương đồng chuỗi
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, user_input, correct_line).ratio()
        
        # Nếu độ tương đồng >= 70% thì coi là đúng
        if similarity >= 0.7:
            # Cập nhật index
            self.active_songs[message.channel.id]['current_line_index'] = next_index
            
            # Kiểm tra xem còn dòng tiếp theo không
            if next_index + 1 < len(song['lines']):
                # Còn dòng tiếp theo → Bot hát tiếp
                embed = discord.Embed(
                    title="✅ Chính Xác!",
                    description=f"**{song['title']}** - *{song['artist']}*",
                    color=discord.Color.green()
                )
                
                embed.add_field(
                    name="🎵 Dòng tiếp theo:",
                    value=f"*\"{song['lines'][next_index + 1]}\"*",
                    inline=False
                )
                
                embed.set_footer(text=f"Tiếp tục hát nào! ({next_index + 1}/{len(song['lines'])})")
                await message.channel.send(embed=embed)
                
                # Cập nhật index cho lần tiếp theo
                self.active_songs[message.channel.id]['current_line_index'] = next_index + 1
            else:
                # Đã hết bài
                embed = discord.Embed(
                    title="🎊 Hoàn Thành!",
                    description=f"**{song['title']}** - *{song['artist']}*\n\n{message.author.mention} đã hoàn thành bài hát! 🎉",
                    color=discord.Color.gold()
                )
                embed.set_footer(text="Dùng !sing để hát bài khác!")
                await message.channel.send(embed=embed)
                
                # Xóa game hát
                del self.active_songs[message.channel.id]
        else:
            # Sai rồi
            embed = discord.Embed(
                title="❌ Sai Rồi!",
                description=f"**Dòng đúng là:** *\"{song['lines'][next_index]}\"*\n\nGame kết thúc!",
                color=discord.Color.red()
            )
            embed.set_footer(text="Dùng !sing để thử lại!")
            await message.channel.send(embed=embed)
            
            # Xóa game hát
            del self.active_songs[message.channel.id]








    @commands.command(name='stopsing', aliases=['stophat'])
    async def stop_sing(self, ctx):
        """Dừng game hát hiện tại"""
        if ctx.channel.id not in self.active_songs:
            await ctx.send("❌ Không có game hát nào đang chạy trong kênh này!")
            return
        
        song_data = self.active_songs[ctx.channel.id]
        song = song_data['song']
        
        embed = discord.Embed(
            title="🛑 Đã Dừng Game Hát",
            description=f"**{song['title']}** - *{song['artist']}*\n\nGame hát đã được dừng lại!",
            color=discord.Color.red()
        )
        embed.set_footer(text="Dùng !sing để bắt đầu lại!")
        await ctx.send(embed=embed)
        
        del self.active_songs[ctx.channel.id]

    @commands.command(name='skip')
    @is_allowed_channel()
    async def skip_question(self, ctx):
        """Bỏ qua câu hỏi hiện tại trong superquiz (chỉ khi có 20 tin nhắn không ai trả lời đúng)"""
        
        # Kiểm tra xem có phải kênh superquiz không
        if not self.superquiz_active or not self.superquiz_channel or ctx.channel.id != self.superquiz_channel.id:
            embed = discord.Embed(
                title="❌ Không Thể Skip",
                description="Lệnh `!skip` chỉ có thể dùng trong kênh đang chạy Super Quiz!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem đã đủ 20 tin nhắn chưa
        if not self.skip_enabled:
            remaining = 20 - self.superquiz_message_count
            embed = discord.Embed(
                title="❌ Chưa Thể Skip",
                description=f"Cần có **20 tin nhắn** không ai trả lời đúng mới có thể skip.\n\nHiện tại: **{self.superquiz_message_count}/20** tin nhắn\nCòn lại: **{remaining}** tin nhắn",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem có quiz đang chạy không
        if ctx.channel.id not in self.active_quizzes:
            embed = discord.Embed(
                title="❌ Không Có Câu Hỏi",
                description="Không có câu hỏi nào đang chạy để skip!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Lấy thông tin câu hỏi
        quiz_data = self.active_quizzes[ctx.channel.id]
        question_index = quiz_data['question_index']
        correct_answer = quiz_data['answer']
        
        # Đánh dấu câu hỏi đã sử dụng (nhưng không đánh dấu trả lời đúng)
        self.used_questions.add(question_index)
        self.save_used_questions()
        
        # Xóa quiz hiện tại
        del self.active_quizzes[ctx.channel.id]
        
        # Reset đếm tin nhắn và skip
        self.superquiz_message_count = 0
        self.skip_enabled = False
        
        # Tính số câu còn lại
        remaining = len(self.questions) - len(self.used_questions)
        
        # Thông báo skip
        embed = discord.Embed(
            title=f"⏭️ Đã Skip Câu #{question_index + 1}",
            description=f"**Đáp án:** {correct_answer}\n\n➡️ Đang chuyển sang câu tiếp theo...",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Còn lại {remaining} câu hỏi")
        await ctx.send(embed=embed)
        
        # Đợi 2 giây rồi chạy câu tiếp theo
        await asyncio.sleep(2)
        await self.start_quiz(ctx.channel, is_superquiz=True)

    @commands.command(name='time')
    @is_allowed_channel()
    async def start_timer(self, ctx):
        """Bật hẹn giờ tự động cho superquiz (tự động chuyển câu sau 7 giây)"""
        
        # Kiểm tra xem có phải kênh superquiz không
        if not self.superquiz_active or not self.superquiz_channel or ctx.channel.id != self.superquiz_channel.id:
            embed = discord.Embed(
                title="❌ Không Thể Bật Timer",
                description="Lệnh `!time` chỉ có thể dùng trong kênh đang chạy Super Quiz!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem timer đã bật chưa
        if self.timer_active:
            embed = discord.Embed(
                title="⚠️ Timer Đã Bật",
                description="Hẹn giờ tự động đã được kích hoạt rồi!\n\nDùng `!stoptime` để tắt (cần 3 người vote).",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Bật timer
        self.timer_active = True
        self.stoptime_votes.clear()
        
        embed = discord.Embed(
            title="⏰ Đã Bật Hẹn Giờ Tự Động!",
            description="✅ Câu hỏi sẽ tự động chuyển sau **7 giây**\n\n📌 Đặc điểm:\n- Tự động chuyển câu mới sau 7s nếu không ai trả lời đúng\n- Vẫn chuyển ngay khi có người trả lời đúng\n- Timer sẽ reset mỗi khi có câu mới\n\n🛑 Dùng `!stoptime` để tắt (cần 3 người vote)",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        
        # Bắt đầu timer cho câu hiện tại nếu có
        if ctx.channel.id in self.active_quizzes:
            await self.start_question_timer(ctx.channel)
    
    @commands.command(name='stoptime')
    @is_allowed_channel()
    async def stop_timer(self, ctx):
        """Tắt hẹn giờ tự động (cần 3 người vote)"""
        
        # Kiểm tra xem có phải kênh superquiz không
        if not self.superquiz_active or not self.superquiz_channel or ctx.channel.id != self.superquiz_channel.id:
            embed = discord.Embed(
                title="❌ Không Thể Tắt Timer",
                description="Lệnh `!stoptime` chỉ có thể dùng trong kênh đang chạy Super Quiz!",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Kiểm tra xem timer có đang bật không
        if not self.timer_active:
            embed = discord.Embed(
                title="⚠️ Timer Chưa Bật",
                description="Hẹn giờ tự động chưa được kích hoạt!\n\nDùng `!time` để bật.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        # Thêm vote
        user_id = ctx.author.id
        if user_id in self.stoptime_votes:
            embed = discord.Embed(
                title="⚠️ Đã Vote Rồi",
                description=f"Bạn đã vote để tắt timer rồi!\n\n📊 Hiện tại: **{len(self.stoptime_votes)}/3** votes",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return
        
        self.stoptime_votes.add(user_id)
        
        # Kiểm tra đủ 3 vote chưa
        if len(self.stoptime_votes) >= 3:
            # Tắt timer
            if self.timer_task and not self.timer_task.done():
                self.timer_task.cancel()
            
            self.timer_active = False
            self.timer_task = None
            self.stoptime_votes.clear()
            
            embed = discord.Embed(
                title="🛑 Đã Tắt Hẹn Giờ!",
                description="Hẹn giờ tự động đã được tắt!\n\n✅ Đủ 3 người vote\n\nDùng `!time` để bật lại.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            # Chưa đủ vote
            remaining = 3 - len(self.stoptime_votes)
            embed = discord.Embed(
                title="📊 Đã Vote Tắt Timer",
                description=f"{ctx.author.mention} đã vote để tắt timer!\n\n**{len(self.stoptime_votes)}/3** votes\nCần thêm **{remaining}** vote nữa.",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
    
    async def start_question_timer(self, channel):
        """Bắt đầu timer 30 giây cho câu hỏi hiện tại"""
        if not self.timer_active:
            return
        
        # Hủy timer cũ nếu có
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()
        
        # Tạo timer mới
        self.timer_task = asyncio.create_task(self.question_timer_countdown(channel))
    
    async def question_timer_countdown(self, channel):
        """Đếm ngược 16 giây và tự động chuyển câu"""
        try:
            await asyncio.sleep(16)  # Đợi 16 giây
            
            # Kiểm tra xem vẫn còn quiz không
            if channel.id not in self.active_quizzes:
                return
            
            # Kiểm tra xem timer vẫn còn active không
            if not self.timer_active:
                return
            
            quiz_data = self.active_quizzes[channel.id]
            question_index = quiz_data['question_index']
            correct_answer = quiz_data['answer']
            
            # Đánh dấu câu hỏi đã sử dụng (không tính trả lời đúng)
            self.used_questions.add(question_index)
            self.save_used_questions()
            
            # Xóa quiz hiện tại
            del self.active_quizzes[channel.id]
            
            # Reset đếm tin nhắn và skip
            self.superquiz_message_count = 0
            self.skip_enabled = False
            self.stoptime_votes.clear()
            
            # Tính số câu còn lại
            remaining = len(self.questions) - len(self.used_questions)
            
            # Thông báo hết giờ
            embed = discord.Embed(
                title=f"⏰ Hết Giờ! - Câu #{question_index + 1}",
                description=f"Không có ai trả lời đúng trong 7 giây.\n**Đáp án:** {correct_answer}\n\n➡️ Đang chuyển sang câu tiếp theo...",
                color=discord.Color.orange()
            )
            embed.set_footer(text=f"Còn lại {remaining} câu hỏi")
            await channel.send(embed=embed)
            
            # Đợi 2 giây rồi chạy câu tiếp theo
            await asyncio.sleep(2)
            await self.start_quiz(channel, is_superquiz=True)
            
        except asyncio.CancelledError:
            # Timer bị hủy (có người trả lời đúng hoặc tắt timer)
            pass
        except Exception as e:
            print(f"❌ Lỗi trong timer: {e}")


async def setup(bot):
    await bot.add_cog(Game(bot))
