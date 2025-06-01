# Meetup Alarm

A Python script that scrapes Meetup events in multiple locations and posts them to a Discord channel weekly.

## Features

- Scrapes Meetup events directly from the website
- Supports multiple locations with different configurations
- Filters events for this week and next week
- Posts formatted event information to Discord
- Configurable search terms per location
- **Configurable schedule:** Post events weekly at a specific day/time (see config)
- **Immediate run:** Use `--now` to post events immediately
- **Systemd service:** Run as a restartable background service
- Error handling and logging
- Runs automatically every week

## Setup

1. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Create a `.env` file with the following variables:
   ```
   DISCORD_TOKEN=your_discord_bot_token_here
   ```

3. Create a `config.yaml` file with your location configurations and schedule:
   ```yaml
   meetup_configs:
     post_day: "Monday"         # Day of week to post (e.g., Monday)
     post_time: "09:30"         # Time to post (24h format, e.g., 09:30)
     timezone: "America/Denver" # Timezone for scheduling
     discord_channel_id: "YOUR_COLORADO_TECH_CHANNEL_ID"
     locations:
       - name: "Denver Tech"
         icon: "🌄"
         search_terms:
           - "tech"
           - "developer"
           - "coding"
           - "startup"
           - "founder"
           - "tech startup"
           - "founders"
         location: "Denver, CO"
         radius: 40
   ```

   **Note:** Do not commit `.env` or `config.yaml` to version control. They are included in `.gitignore`.

4. Set up Discord Bot:
   - Go to https://discord.com/developers/applications
   - Click "New Application" and give it a name
   - Go to the "Bot" section and click "Add Bot"
   - Under the bot's token, click "Copy" to get your bot token
   - Enable the "Message Content Intent" under Privileged Gateway Intents
   - Go to OAuth2 -> URL Generator
   - Select "bot" under scopes
   - Select "Send Messages" under bot permissions
   - Use the generated URL to invite the bot to your server
   - Get your channel ID by enabling Developer Mode in Discord settings, then right-clicking the channel and selecting "Copy ID"

## Usage

### Run immediately (one-time post)
```bash
python meetup_alarm.py --now
```

### Run as a scheduled service (recommended)
- The bot will run continuously and post events at the configured day/time each week.

### Systemd Service Setup
1. Copy `meetup-bot.service` to your project directory.
2. Run the install script:
   ```bash
   bash install.sh
   ```
   This will:
   - Move the service file to `/etc/systemd/system/`
   - Reload systemd
   - Enable and start the service

3. Check the service status:
   ```bash
   sudo systemctl status meetup-bot
   ```
4. View logs:
   ```bash
   sudo journalctl -u meetup-bot -f
   ```

## Output Format

The script will post a message to Discord with the following format:

```
# [SearchTerm] Event Name
**Group Name** | **This Monday, 02 June - 17:30**

[Add to Google Calendar] [Check Location]

(Buttons below the message)
```

## Notes

- The script uses web scraping to fetch events, so it's more resilient to API changes
- Random delays are added between requests to be respectful to Meetup's servers
- Events are deduplicated based on their URLs
- The script includes error handling for network issues and parsing problems
- `.env` and `config.yaml` are ignored by git for security 