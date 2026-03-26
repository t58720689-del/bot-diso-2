# Hướng Dẫn Giới Hạn Bot Trong Các Kênh Cụ Thể

## Cách Lấy Channel ID trong Discord

1. **Bật Developer Mode**:
   - Mở Discord → Settings (⚙️)
   - Vào **Advanced** (Nâng cao)
   - Bật **Developer Mode** (Chế độ nhà phát triển)

2. **Lấy ID của Channel**:
   - Click chuột phải vào tên kênh chat
   - Chọn **Copy ID** (hoặc **Copy Channel ID**)
   - ID sẽ có dạng số dài, ví dụ: `1234567890123456789`

## Cấu Hình Channel Được Phép

Mở file `config.py` và tìm dòng `ALLOWED_CHANNELS`:

```python
# Danh sách ID các kênh được phép sử dụng bot
# Thay thế các ID này bằng ID kênh của bạn
ALLOWED_CHANNELS = [
    1234567890123456789,  # Thay thế bằng ID channel 1
    9876543210987654321,  # Thay thế bằng ID channel 2
    # Thêm ID channel khác tại đây
]
```

### Ví Dụ Cấu Hình:

```python
ALLOWED_CHANNELS = [
    1446865411814588426,  # Kênh general
    1447123456789012345,  # Kênh bot-commands
    1447987654321098765,  # Kênh game
]
```

### Cho Phép Bot Hoạt Động Ở Tất Cả Các Kênh:

Nếu bạn muốn bot hoạt động ở tất cả các kênh, để danh sách rỗng:

```python
ALLOWED_CHANNELS = []
```

## Các Tính Năng Bị Giới Hạn

Khi bạn giới hạn channel, các tính năng sau CHỈ hoạt động trong các channel được chỉ định:

### Commands Chat & AI:
- `!ai` / `!chat` / `!ask` - Chat với AI
- `!clear` / `!reset` - Xóa lịch sử chat với AI


### Commands Game & Quiz:
- `!quiz` - Bắt đầu quiz trắc nghiệm
- `!superquiz` - Bắt đầu super quiz (nhiều câu hỏi liên tiếp)
- `!44stopsuperquiz` - Dừng super quiz
- `!bxh` / `!leaderboard` / `!top` / `!rank` - Xem bảng xếp hạng
- `!khoga` / `!mykhoga` / `!inventory` / `!tuido` - Xem khô gà (túi đồ) của bạn
- `!check` / `!diem` / `!score` - Kiểm tra điểm số của bạn
- `!quizprogress` / `!progress` / `!tiendo` - Xem tiến độ quiz
- `!sing` / `!hat` - Bot hát bài hát ngẫu nhiên
- `!stopsing` / `!stophat` - Dừng hát

### Commands Admin:
- `!resetquiz1` - Reset câu hỏi quiz đã dùng (admin)
- `!modresetquiz` - Moderator reset quiz (admin)

### Commands Khác:
- `!hello` - Chào hỏi đơn giản

### Auto-responses (Tự động phản hồi):
- Chào hỏi khi có từ: chào, hi, hello, hey...
- Phản hồi khi nhắc "anh độ"
- Phản hồi khi có "khô gà1"
- Phản hồi khi có "xây trường", "xây nhà", "việc tốt anh độ"
- Phản hồi khi có "uia"
- Tóm tắt chat tự động

### Các Tính Năng VẪN Hoạt Động Ở Mọi Kênh:
- Xóa tin nhắn của người bị timeout
- Xóa tin nhắn chứa từ cấm (chó độ, độ ngu, từ cha...)

## Thông Báo Cho User

Khi user thử dùng lệnh ở kênh không được phép, bot sẽ hiện thông báo:

> ⚠️ Bot chỉ hoạt động trong các kênh được chỉ định. Kênh này không được phép sử dụng bot.

(Thông báo sẽ tự động xóa sau 10 giây)

## Lưu Ý

- Sau khi thay đổi `config.py`, cần **khởi động lại bot** để áp dụng thay đổi
- ID channel phải là số nguyên, không có dấu ngoặc kép
- Có thể thêm nhiều channel ID tùy ý, phân cách bằng dấu phẩy
- Nếu để `ALLOWED_CHANNELS = []`, bot sẽ hoạt động ở tất cả các kênh như bình thường

## Kiểm Tra Bot

Sau khi cấu hình:

1. Khởi động lại bot
2. Thử dùng lệnh `!hello` ở kênh ĐƯỢC phép → Bot phản hồi
3. Thử dùng lệnh `!hello` ở kênh KHÔNG được phép → Bot hiện thông báo cảnh báo
