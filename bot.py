import os
import logging
import sqlite3
import sys
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import Conflict, TelegramError

# --- Configuration ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN environment variable not set!")
    sys.exit(1)

ADMIN_IDS = []
admin_ids_str = os.environ.get("ADMIN_IDS", "")
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (group_id INTEGER PRIMARY KEY,
                  group_name TEXT,
                  added_date TIMESTAMP,
                  active INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS announcements
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  group_id INTEGER,
                  message TEXT,
                  sent_date TIMESTAMP,
                  status TEXT,
                  recipients INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS pending_announcements
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  group_id INTEGER,
                  message TEXT,
                  created_date TIMESTAMP,
                  scheduled_time TIMESTAMP)''')
    conn.commit()
    conn.close()
    logger.info("Database initialized")

init_db()

# --- Database Helper Functions ---
def add_group(group_id, group_name):
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO groups (group_id, group_name, added_date, active) VALUES (?, ?, ?, 1)",
              (group_id, group_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    logger.info(f"Group {group_id} added")

def remove_group(group_id):
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("UPDATE groups SET active=0 WHERE group_id=?", (group_id,))
    conn.commit()
    conn.close()

def get_active_groups():
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("SELECT group_id, group_name FROM groups WHERE active=1")
    groups = c.fetchall()
    conn.close()
    return groups

def save_announcement(group_id, message, status, recipients):
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO announcements (group_id, message, sent_date, status, recipients) VALUES (?, ?, ?, ?, ?)",
              (group_id, message, datetime.now().isoformat(), status, recipients))
    conn.commit()
    conn.close()

def get_group_count():
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM groups WHERE active=1")
    count = c.fetchone()[0]
    conn.close()
    return count

def save_pending_announcement(group_id, message, scheduled_time=None):
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO pending_announcements (group_id, message, created_date, scheduled_time) VALUES (?, ?, ?, ?)",
              (group_id, message, datetime.now().isoformat(), scheduled_time))
    conn.commit()
    conn.close()

def get_pending_announcements():
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("SELECT id, group_id, message, scheduled_time FROM pending_announcements")
    pending = c.fetchall()
    conn.close()
    return pending

def delete_pending_announcement(announcement_id):
    conn = sqlite3.connect('announcement_bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM pending_announcements WHERE id=?", (announcement_id,))
    conn.commit()
    conn.close()

# --- Helper Functions ---
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    
    user_id = update.effective_user.id
    
    if user_id in ADMIN_IDS:
        return True
    
    if update.message and update.message.chat.type in ['group', 'supergroup']:
        try:
            chat_member = await context.bot.get_chat_member(
                update.message.chat.id, 
                user_id
            )
            if chat_member.status in ['administrator', 'creator']:
                return True
        except Exception as e:
            logger.error(f"Error checking admin: {e}")
    
    return False

# --- Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    welcome_text = (
        f"🤖 **Announcement Bot**\n\n"
        f"Hi {user.first_name}! I broadcast messages to groups.\n\n"
        f"**Commands:**\n"
        f"/announce <message> - Send to all groups\n"
        f"/schedule <time> <message> - Schedule announcement\n"
        f"/groups - List active groups\n"
        f"/stats - View statistics\n"
        f"/help - Show this message\n\n"
        f"**Admin Only:**\n"
        f"/broadcast <message> - Super admin broadcast\n"
        f"/removegroup <group_id> - Remove a group"
    )
    
    if chat.type in ['group', 'supergroup']:
        add_group(chat.id, chat.title or "Unknown Group")
        welcome_text += f"\n\n✅ This group has been added!"
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Announcement Bot Help**\n\n"
        "**Commands:**\n"
        "/announce <message> - Send announcement to all groups\n"
        "/schedule <time> <message> - Schedule announcement\n"
        "/groups - List all active groups\n"
        "/stats - View bot statistics\n"
        "/help - Show this message\n\n"
        "**Admin Only:**\n"
        "/broadcast <message> - Send to all groups\n"
        "/removegroup <group_id> - Remove a group\n\n"
        "**Example:**\n"
        "/announce Meeting at 4 PM today!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def announce_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⚠️ This command is for admins only!")
        return
    
    message_text = ' '.join(context.args)
    
    if not message_text:
        await update.message.reply_text(
            "❌ Please provide a message!\n"
            "Usage: /announce <your message>"
        )
        return
    
    groups = get_active_groups()
    
    if not groups:
        await update.message.reply_text("❌ No active groups found!")
        return
    
    status_msg = await update.message.reply_text(
        f"📢 Sending to {len(groups)} groups...\n"
        f"Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}"
    )
    
    success_count = 0
    fail_count = 0
    failed_groups = []
    
    for group_id, group_name in groups:
        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=f"📢 **ANNOUNCEMENT**\n\n{message_text}",
                parse_mode='Markdown'
            )
            success_count += 1
            logger.info(f"Sent to group {group_id}")
            
        except Exception as e:
            fail_count += 1
            failed_groups.append((group_id, group_name))
            logger.error(f"Failed to send to {group_id}: {e}")
            
            if "chat not found" in str(e) or "bot was kicked" in str(e):
                remove_group(group_id)
    
    save_announcement(0, message_text, "sent", success_count)
    
    response = f"✅ Announcement sent!\n\n"
    response += f"📊 **Statistics:**\n"
    response += f"• Successfully sent: {success_count}\n"
    response += f"• Failed: {fail_count}\n"
    response += f"• Total groups: {len(groups)}"
    
    if failed_groups:
        response += f"\n\n⚠️ Failed groups (deactivated):\n"
        for g_id, g_name in failed_groups[:5]:
            response += f"• {g_name} ({g_id})\n"
    
    await status_msg.edit_text(response, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Super admin only!")
        return
    
    await announce_command(update, context)

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⚠️ Admins only!")
        return
    
    groups = get_active_groups()
    
    if not groups:
        await update.message.reply_text("📭 No active groups found.")
        return
    
    response = f"📋 **Active Groups ({len(groups)})**\n\n"
    for i, (group_id, group_name) in enumerate(groups, 1):
        response += f"{i}. {group_name} (ID: {group_id})\n"
    
    await update.message.reply_text(response, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⚠️ Admins only!")
        return
    
    group_count = get_group_count()
    pending = get_pending_announcements()
    
    stats = (
        f"📊 **Bot Statistics**\n\n"
        f"• Active Groups: {group_count}\n"
        f"• Pending Announcements: {len(pending)}\n"
        f"• Bot Status: 🟢 Online\n\n"
        f"**Admin Info:**\n"
        f"• Admin IDs: {len(ADMIN_IDS)} configured"
    )
    
    await update.message.reply_text(stats, parse_mode='Markdown')

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("⚠️ Admins only!")
        return
    
    args = context.args
    
    if len(args) < 2:
        await update.message.reply_text(
            "❌ Please provide time and message!\n"
            "Usage: /schedule <time> <message>\n"
            "Example: /schedule 15:30 Meeting at 4 PM"
        )
        return
    
    time_str = args[0]
    message = ' '.join(args[1:])
    
    try:
        hour, minute = map(int, time_str.split(':'))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
        scheduled_time = f"{hour:02d}:{minute:02d}"
    except:
        await update.message.reply_text(
            "❌ Invalid time format!\n"
            "Use HH:MM format (e.g., 15:30)"
        )
        return
    
    save_pending_announcement(0, message, scheduled_time)
    
    await update.message.reply_text(
        f"✅ Announcement scheduled for {scheduled_time}!\n\n"
        f"📝 Message: {message[:100]}{'...' if len(message) > 100 else ''}"
    )

async def remove_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⚠️ Super admin only!")
        return
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "❌ Please provide a group ID!\n"
            "Usage: /removegroup <group_id>"
        )
        return
    
    group_id = int(args[0])
    remove_group(group_id)
    await update.message.reply_text(f"✅ Group {group_id} has been removed.")

# --- Group Event Handlers ---
async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    chat = update.message.chat
    
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            add_group(chat.id, chat.title or "Unknown Group")
            await update.message.reply_text(
                f"🤖 Hello! I'm the Announcement Bot.\n\n"
                f"✅ This group has been added to my announcement list.\n"
                f"Use /help to see available commands."
            )
            break

async def left_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    chat = update.message.chat
    
    if update.message.left_chat_member and update.message.left_chat_member.id == context.bot.id:
        remove_group(chat.id)
        logger.info(f"Bot removed from group {chat.id}")

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    
    if isinstance(context.error, Conflict):
        logger.warning("Conflict error - another instance running")

# --- Main Function ---
def main():
    """Start the bot."""
    logger.info("🚀 Starting Announcement Bot...")
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("announce", announce_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("groups", groups_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("schedule", schedule_command))
    application.add_handler(CommandHandler("removegroup", remove_group_command))
    
    # Add group event handlers
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member_handler))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    logger.info("✅ Bot is ready!")
    
    # Clear webhook
    application.bot.delete_webhook()
    
    # Start polling
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Conflict as e:
        logger.error(f"Conflict error: {e}")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
