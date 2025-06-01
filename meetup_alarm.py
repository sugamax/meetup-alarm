import os
import requests
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
import yaml
from typing import Dict, List
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import time
import random
import urllib.parse
import asyncio
import json
import re
import pytz
import calendar
import argparse

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Load config
try:
    with open('config.yaml', 'r') as file:
        CONFIG = yaml.safe_load(file)
except Exception as e:
    print(f"Error loading configuration: {str(e)}")
    raise

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('error.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MeetupConfig:
    def __init__(self, config: Dict):
        self.name = config['name']
        self.icon = config.get('icon', '🎉')
        self.search_terms = config['search_terms']
        self.location = config['location']
        self.radius = config.get('radius', 25)

class CreateEventButton(discord.ui.View):
    def __init__(self, event_data, calendar_url, location_url=None):
        super().__init__(timeout=None)  # Button never times out
        self.event_data = event_data
        # Remove '[Meetup]' prefix from the button label
        self.add_item(discord.ui.Button(label="Add to Google Calendar", style=discord.ButtonStyle.secondary, emoji="🗓️", url=calendar_url))
        # Add Check Location button with pin icon if location_url is provided
        if location_url:
            self.add_item(discord.ui.Button(label="Check Location", style=discord.ButtonStyle.secondary, emoji="📍", url=location_url))

    @discord.ui.button(label="Create Discord Event", style=discord.ButtonStyle.primary, emoji="✅")
    async def create_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)

            # Prepare event data for external event
            event_url = self.event_data.get('url')
            event_name = self.event_data['name']
            start_time = self.event_data['start_time']
            end_time = self.event_data['end_time']
            location = self.event_data['location']
            # Set the description to the Meetup event link
            description = event_url if event_url else ''

            # Create the event as an external event
            event = await interaction.guild.create_scheduled_event(
                name=event_name,
                start_time=start_time,
                end_time=end_time,
                entity_type=discord.EntityType.external,
                location=location,
                description=description,
                privacy_level=discord.PrivacyLevel.guild_only
            )

            button.disabled = True
            button.label = "Event Created!"
            await interaction.message.edit(view=self)
            await interaction.followup.send(f"✅ Created Discord event: {event.name}", ephemeral=True)

        except discord.errors.Forbidden as e:
            logger.error(f"Permission error creating Discord event: {e}\nEvent data: {self.event_data}")
            await interaction.followup.send("❌ I don't have permission to create events in this server. Please make sure I have the 'Manage Events' permission.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error creating Discord event: {e}\nEvent data: {self.event_data}")
            await interaction.followup.send("❌ Failed to create Discord event. Please try again later.", ephemeral=True)

class MeetupBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        self.channel = None
        self.meetup_configs = self.load_meetup_configs()
        self.discord_channel_id = int(CONFIG['meetup_configs']['discord_channel_id'])
        self.ua = UserAgent()
        self.post_day = CONFIG['meetup_configs'].get('post_day', 'Monday')
        self.post_time = CONFIG['meetup_configs'].get('post_time', '09:30')
        self.timezone = CONFIG['meetup_configs'].get('timezone', 'America/Denver')
        self.immediate_mode = False

    def load_meetup_configs(self) -> List[MeetupConfig]:
        return [MeetupConfig(cfg) for cfg in CONFIG['meetup_configs']['locations']]

    async def setup_hook(self) -> None:
        logger.info(f"Logged in as {self.user.name}")
        self.bg_task = self.loop.create_task(self.weekly_meetup_task())

    async def on_ready(self):
        logger.info('Bot is ready!')
        self.channel = self.get_channel(self.discord_channel_id)
        if not self.channel:
            logger.error(f"Could not find channel with ID {self.discord_channel_id}")
            await self.close()
            return
        # If running in immediate mode, post events and exit
        if self.immediate_mode:
            await self.meetup_task()
            await self.close()

    async def weekly_meetup_task(self):
        await self.wait_until_ready()
        tz = pytz.timezone(self.timezone)
        while not self.is_closed():
            now = datetime.now(tz)
            # Find next scheduled post time
            post_time = datetime.strptime(self.post_time, "%H:%M").time()
            days_ahead = (getattr(calendar, self.post_day.upper()) - now.weekday()) % 7
            if days_ahead == 0 and now.time() > post_time:
                days_ahead = 7
            next_post = (now + timedelta(days=days_ahead)).replace(hour=post_time.hour, minute=post_time.minute, second=0, microsecond=0)
            wait_seconds = (next_post - now).total_seconds()
            logger.info(f"Next event post scheduled for {next_post} ({wait_seconds/3600:.2f} hours from now)")
            await asyncio.sleep(wait_seconds)
            await self.meetup_task()

    async def meetup_task(self):
        if not self.channel:
            logger.error("Channel not set up")
            return

        logger.info("Starting Meetup event collection task")
        # Get events for each location
        all_events = []
        for config in self.meetup_configs:
            logger.info(f"Processing location: {config.name} ({config.location})")
            # Get events for each search term
            for search_term in config.search_terms:
                events = get_meetup_events(search_term, config.location, config.radius)
                all_events.extend(events)
                logger.info(f"Added {len(events)} events for search term '{search_term}'")
                # Add a small delay between requests to be respectful
                delay = random.uniform(1, 3)
                logger.debug(f"Waiting {delay:.2f} seconds before next request")
                time.sleep(delay)

        if not all_events:
            logger.info("No events found!")
            return

        # Remove duplicates based on event name
        unique_events = {event['title']: event for event in all_events}.values()
        logger.info(f"Found {len(unique_events)} unique events after deduplication")

        # Sort events by date
        sorted_events = sorted(unique_events, key=lambda x: x['time'])

        # Group events by week
        today = datetime.now().astimezone()  # Make today timezone-aware
        this_week = []
        next_week = []

        for event in sorted_events:
            event_time = event['time']
            days_diff = (event_time - today).days

            if 0 <= days_diff <= 7:
                this_week.append(event)
            elif 8 <= days_diff <= 14:
                next_week.append(event)

        logger.info(f"Events grouped: {len(this_week)} this week, {len(next_week)} next week")

        # Create and send header message
        header_message = "# 🎉 Upcoming Tech Events in Colorado 🎉\n\n**This Week's Events:**"
        try:
            await self.channel.send(header_message)
            logger.info("Posted combined header message")
            await asyncio.sleep(2)  # Wait 2 seconds before next message
        except discord.errors.HTTPException as e:
            logger.error(f"Error posting header to Discord: {e}")
            return

        # Group events by search_term
        events_by_search_term = {}
        for event in sorted_events:
            st = event.get('search_term', 'Other')
            if st not in events_by_search_term:
                events_by_search_term[st] = []
            events_by_search_term[st].append(event)

        # For each search_term, post events
        for search_term, events in events_by_search_term.items():
            for event in events:
                try:
                    message_data = format_event_message(event)
                    view = CreateEventButton(message_data['event_data'], message_data['calendar_url'], message_data['location_url'])
                    await self.channel.send(message_data['message'], view=view)
                    logger.info(f"Posted event: {event['title']}")
                    await asyncio.sleep(2)
                except discord.errors.HTTPException as e:
                    logger.error(f"Error posting event to Discord: {e}")
                    continue

        logger.info("Finished posting all events to Discord")

def get_meetup_events(search_term: str, location: str, radius: int) -> List[Dict]:
    """Scrape events from Meetup website."""
    events = []
    # Extract city and state from location (e.g., 'Denver, CO')
    try:
        city, state = [x.strip() for x in location.split(',')]
        city_url = city.replace(' ', '-')
        state_url = state.lower()
        url_city = f"us--{state_url}--{city_url}"
    except Exception as e:
        logger.error(f"Error parsing location '{location}': {e}")
        url_city = "us--co--Denver"  # fallback
    url = f"https://www.meetup.com/find/?suggested=true&source=EVENTS&keywords={urllib.parse.quote(search_term)}&location={url_city}&distance=fiftyMiles"
    logger.info(f"Fetching events from: {url}")
    headers = {
        'User-Agent': UserAgent().random,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        soup = BeautifulSoup(html, 'lxml')
        
        # Find all JSON-LD script tags
        script_tags = soup.find_all('script', {'type': 'application/ld+json'})
        logger.info(f"Found {len(script_tags)} JSON-LD script tags")
        
        # Combine all JSON-LD data
        all_event_data = []
        for i, script in enumerate(script_tags):
            if script.string and len(script.string.strip()) > 0:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        all_event_data.extend(data)
                    else:
                        all_event_data.append(data)
                except json.JSONDecodeError as e:
                    logger.error(f"Error parsing JSON-LD data from script {i}: {e}")
                    continue
        
        if not all_event_data:
            logger.error("Could not find any valid JSON-LD data")
            return events
            
        logger.info(f"Found {len(all_event_data)} total items in JSON-LD data")
        
        for event in all_event_data:
            try:
                # Extract event details from JSON
                title = event.get('name', '')
                url = event.get('url', '')
                time_str = event.get('startDate', '')
                if time_str:
                    # Convert UTC time to local time
                    event_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                    # Convert to local timezone (America/Denver)
                    event_time = event_time.astimezone()
                else:
                    continue
                location_data = event.get('location', {})
                if isinstance(location_data, dict):
                    location = location_data.get('name', '')
                    geo = location_data.get('geo', {})
                    if isinstance(geo, dict):
                        latitude = geo.get('latitude')
                        longitude = geo.get('longitude')
                        if latitude and longitude:
                            location = {
                                'name': location or 'Location TBD',
                                'geo': {
                                    'latitude': latitude,
                                    'longitude': longitude
                                }
                            }
                    if not location and 'address' in location_data:
                        address = location_data['address']
                        if isinstance(address, dict):
                            location = {
                                'name': ', '.join(filter(None, [
                                    address.get('streetAddress', ''),
                                    address.get('addressLocality', ''),
                                    address.get('addressRegion', '')
                                ])),
                                'geo': geo if isinstance(geo, dict) else None
                            }
                else:
                    location = {'name': str(location_data), 'geo': None}
                organizer = event.get('organizer', {})
                if isinstance(organizer, dict):
                    group = organizer.get('name', 'Unknown Group')
                else:
                    group = 'Unknown Group'
                event_attendance_mode = event.get('eventAttendanceMode')
                if event_attendance_mode == 'https://schema.org/OnlineEventAttendanceMode':
                    attendance_mode = 'online'
                else:
                    attendance_mode = 'offline'
                event_data = {
                    'title': title,
                    'url': url,
                    'time': event_time,
                    'location': location,
                    'group': group,
                    'description': event.get('description', ''),
                    'search_term': search_term,  # Track which search term matched
                    'eventAttendanceMode': attendance_mode
                }
                events.append(event_data)
            except Exception as e:
                logger.error(f"Error parsing event data: {e}")
                continue
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Meetup events: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while scraping: {e}")
    logger.info(f"Successfully scraped {len(events)} events for '{search_term}' in {location}")
    return events

def format_event_message(event):
    """Format a single event into a Discord message."""
    # Clean the title by removing markdown and image URLs
    title = event['title']
    
    # Remove markdown image syntax [text](url)
    title = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', title)
    
    # Remove any remaining URLs
    title = re.sub(r'https?://\S+', '', title)
    
    # Remove any remaining markdown formatting
    title = re.sub(r'[*_~`]', '', title)
    
    # Remove any remaining brackets and their contents
    title = re.sub(r'\[.*?\]', '', title)
    
    # Remove any remaining parentheses and their contents
    title = re.sub(r'\(.*?\)', '', title)
    
    # Remove any remaining special characters
    title = re.sub(r'[^\w\s\-.,!?]', '', title)
    
    # Remove any extra whitespace
    title = ' '.join(title.split())
    
    # Prepend search_term to the title for the Discord message only
    search_term = event.get('search_term')
    display_title = title
    if search_term:
        display_title = f"[{search_term.capitalize()}] {title}"
    # Create a clean title and make it a clickable link
    clean_title = f"[{display_title}]({event['url']})"
    meetup_title = f"[Meetup] {title}"
    
    # Clean and truncate the description
    description = event.get('description', '')
    # Remove markdown formatting
    description = re.sub(r'[*_~`]', '', description)
    # Remove URLs
    description = re.sub(r'https?://\S+', '', description)
    # Remove extra whitespace and newlines
    description = ' '.join(description.split())
    # Truncate to 200 characters and add ellipsis if needed
    if len(description) > 200:
        description = description[:197] + '...'
    
    # Format location
    location = event['location']
    location_text = location['name'] if isinstance(location, dict) else location
    
    # Create Google Calendar link
    event_time = event['time']
    # Format dates for Google Calendar URL
    start_date = event_time.strftime('%Y%m%dT%H%M%S')
    end_date = (event_time + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')
    
    # Create the Google Calendar URL
    calendar_url = (
        f"https://calendar.google.com/calendar/render"
        f"?action=TEMPLATE"
        f"&text={urllib.parse.quote(meetup_title)}"
        f"&dates={start_date}/{end_date}"
    )
    if location_text and str(location_text).strip():
        calendar_url += f"&location={urllib.parse.quote(location_text)}"
        location_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(location_text)}"
    else:
        location_url = None
    
    # Format the date
    today = datetime.now().astimezone()
    days_diff = (event_time - today).days
    
    # Get the day name and formatted date
    day_name = event_time.strftime('%A')
    date_str = event_time.strftime('%d %B')
    time_str = event_time.strftime('%H:%M')
    
    # Determine if it's this week or next week
    if days_diff <= 7:
        date_text = f"This {day_name}, {date_str} - {time_str}"
    else:
        date_text = f"Next Week {day_name}, {date_str} - {time_str}"

    # Inline links to prevent Discord unfurling
    links = ""
    # Do not add the Check Location link here; only show as a button


    # Add online indicator if it's an online event
    online_text = " | ☎️ Online" if event.get('eventAttendanceMode') == 'online' else ""

    message = (
        f"# {clean_title}\n"
        f"**{event['group']}** | **{date_text}**{online_text}\n"
        f"\n"  # Add an extra empty line after the date
    )
    
    # Return both the message and the event data needed for the button
    return {
        'message': message,
        'event_data': {
            'name': meetup_title,
            'start_time': event_time,
            'end_time': event_time + timedelta(hours=1),
            'location': location_text if location_text and str(location_text).strip() else None,
            'description': description,
            'url': event['url']
        },
        'calendar_url': calendar_url,
        'location_url': location_url
    }

def main():
    parser = argparse.ArgumentParser(description="Meetup Discord Bot")
    parser.add_argument('--now', action='store_true', help='Post events immediately and exit')
    args = parser.parse_args()

    if not DISCORD_TOKEN:
        logger.error("Discord token not configured!")
        return

    bot = MeetupBot()
    if args.now:
        bot.immediate_mode = True
        async def run_once():
            await bot.start(DISCORD_TOKEN, reconnect=False)
        asyncio.run(run_once())
    else:
        bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main() 