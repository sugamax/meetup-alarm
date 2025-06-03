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
import uuid
import traceback
import sqlite3

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

class CreateEventButton(discord.ui.Button):
    def __init__(self, event_uuid, calendar_url, location_url, disabled=False):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="Create Discord Event" if not disabled else "Event Created!",
            emoji="✅",
            custom_id=f"create_event_{event_uuid}" if event_uuid else "create_event_*",
            disabled=disabled
        )
        self.event_uuid = event_uuid
        self.calendar_url = calendar_url
        self.location_url = location_url

    async def callback(self, interaction: discord.Interaction):
        try:
            logger.info("=== CreateEventButton callback started ===")
            logger.info(f"Button custom_id: {self.custom_id}")
            logger.info(f"Event UUID: {self.event_uuid}")
            logger.info(f"Interaction user: {interaction.user.name} (ID: {interaction.user.id})")
            logger.info(f"Interaction message ID: {interaction.message.id}")
            
            # Extract UUID from custom_id
            custom_id = interaction.data.get('custom_id', '')
            event_uuid = custom_id.replace('create_event_', '')
            
            # Get event data from bot's event_data_map
            bot = interaction.client
            event_data = bot.event_data_map.get(event_uuid)
            
            if not event_data:
                logger.error(f"Event data not found for UUID: {event_uuid}")
                await interaction.response.send_message("❌ Error: Event data not found. The bot may have been restarted.", ephemeral=True)
                return
            
            # Log event data for debugging
            logger.info(f"Creating Discord event with data: {event_data}")
            logger.info(f"Start time: {event_data['start_time']} (tzinfo: {event_data['start_time'].tzinfo})")
            logger.info(f"End time: {event_data['end_time']} (tzinfo: {event_data['end_time'].tzinfo})")
            
            # Ensure timezone awareness
            if event_data['start_time'].tzinfo is None:
                event_data['start_time'] = pytz.timezone(bot.timezone).localize(event_data['start_time'])
            if event_data['end_time'].tzinfo is None:
                event_data['end_time'] = pytz.timezone(bot.timezone).localize(event_data['end_time'])
            
            # Create the scheduled event
            scheduled_event = await interaction.guild.create_scheduled_event(
                name=f"[Meetup] {event_data['title']}",
                description=event_data['url'],
                start_time=event_data['start_time'],
                end_time=event_data['end_time'],
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location=event_data.get('location', 'Online')
            )
            
            # Always generate calendar and location URLs from event_data
            title = event_data['title']
            event_time = event_data['start_time']
            start_date = event_time.strftime('%Y%m%dT%H%M%S')
            end_date = (event_time + timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')
            calendar_title = title[:100]
            calendar_url = (
                f"https://calendar.google.com/calendar/render"
                f"?action=TEMPLATE"
                f"&text={urllib.parse.quote(calendar_title)}"
                f"&dates={start_date}/{end_date}"
                f"&details={urllib.parse.quote(event_data['url'])}"
            )
            location_text = event_data.get('location', '')
            if location_text and str(location_text).strip():
                location_text_truncated = str(location_text)[:100]
                calendar_url += f"&location={urllib.parse.quote(location_text_truncated)}"
                location_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(location_text_truncated)}"
            else:
                location_url = None

            # Determine if the event is online
            is_online = False
            attendance_mode = event_data.get('eventAttendanceMode')
            if attendance_mode == 'online' or (isinstance(attendance_mode, str) and 'online' in attendance_mode.lower()):
                is_online = True

            # Rebuild the view with the button disabled and correct URLs
            new_view = CreateEventView(event_uuid, calendar_url, location_url, disabled=True, location_text=location_text)
            await interaction.message.edit(view=new_view)
            
            # Send confirmation
            await interaction.response.send_message(f"✅ Created Discord event: [Meetup] {event_data['title']}", ephemeral=True)
            
            # Remove event data after successful creation
            del bot.event_data_map[event_uuid]
            bot.delete_event_data(event_uuid)
            
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            logger.error(traceback.format_exc())
            await interaction.response.send_message(f"❌ Error creating event: {str(e)}", ephemeral=True)

class CalendarButton(discord.ui.Button):
    def __init__(self, calendar_url):
        super().__init__(
            style=discord.ButtonStyle.link,
            label="Add to Google Calendar",
            emoji="📅",
            url=calendar_url
        )

class LocationButton(discord.ui.Button):
    def __init__(self, location_url):
        super().__init__(
            style=discord.ButtonStyle.link,
            label="Check Location",
            emoji="📍",
            url=location_url
        )

class CreateEventView(discord.ui.View):
    def __init__(self, event_uuid, calendar_url, location_url, disabled=False, location_text=None):
        super().__init__(timeout=None)  # No timeout for persistent view
        self.add_item(CreateEventButton(event_uuid, calendar_url, location_url, disabled=disabled))
        if calendar_url:
            self.calendar_button = CalendarButton(calendar_url)
            self.add_item(self.calendar_button)
        # Only add location button if location_text is not 'Online'
        if location_url and location_text and location_text.strip().lower() != 'online':
            self.location_button = LocationButton(location_url)
            self.add_item(self.location_button)

    @classmethod
    async def from_uuid(cls, event_uuid: str, calendar_url: str, location_url: str, disabled=False, location_text=None):
        """Create a view instance from a UUID"""
        view = cls(event_uuid, calendar_url, location_url, disabled=disabled, location_text=location_text)
        return view

class MeetupBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guild_scheduled_events = True
        super().__init__(command_prefix='!', intents=intents)
        self.channel = None
        self.meetup_configs = self.load_meetup_configs()
        self.discord_channel_id = int(CONFIG['meetup_configs']['discord_channel_id'])
        self.ua = UserAgent()
        self.post_day = CONFIG['meetup_configs'].get('post_day', 'Monday')
        self.post_time = CONFIG['meetup_configs'].get('post_time', '09:30')
        self.timezone = CONFIG['meetup_configs'].get('timezone', 'America/Denver')
        self.immediate_mode = False
        self.event_data_map = {}
        self.db_path = 'events.db'
        self.init_db()  # Initialize database on startup
        self.load_event_data()  # Load event data from database

    def init_db(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    uuid TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    location TEXT,
                    description TEXT,
                    url TEXT NOT NULL,
                    calendar_url TEXT,
                    location_url TEXT
                )
            ''')
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            logger.error(traceback.format_exc())

    def load_event_data(self):
        """Load event data from SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('SELECT * FROM events')
            rows = c.fetchall()
            
            for row in rows:
                uuid, title, start_time, end_time, location, description, url, calendar_url, location_url = row
                try:
                    # Parse datetime strings with timezone info
                    start_dt = datetime.fromisoformat(start_time)
                    end_dt = datetime.fromisoformat(end_time)
                    
                    # Ensure timezone awareness
                    if start_dt.tzinfo is None:
                        start_dt = pytz.timezone(self.timezone).localize(start_dt)
                    if end_dt.tzinfo is None:
                        end_dt = pytz.timezone(self.timezone).localize(end_dt)
                    
                    # Log the loaded event data for debugging
                    logger.info(f"Loading event {uuid}:")
                    logger.info(f"  Title: {title}")
                    logger.info(f"  Start time: {start_dt} (tzinfo: {start_dt.tzinfo})")
                    logger.info(f"  End time: {end_dt} (tzinfo: {end_dt.tzinfo})")
                    
                    self.event_data_map[uuid] = {
                        'title': title,
                        'start_time': start_dt,
                        'end_time': end_dt,
                        'location': location,
                        'description': description,
                        'url': url,
                        'calendar_url': calendar_url,
                        'location_url': location_url
                    }
                except ValueError as e:
                    logger.error(f"Error parsing datetime for event {uuid}: {e}")
                    continue
                    
            conn.close()
            logger.info(f"Loaded {len(self.event_data_map)} events from database")
        except Exception as e:
            logger.error(f"Error loading event data from database: {e}")
            logger.error(traceback.format_exc())
            self.event_data_map = {}

    def save_event_data(self, event_uuid, event_data):
        """Save event data to SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Convert datetime objects to ISO format strings with timezone info
            event_data_copy = event_data.copy()
            # Ensure datetime objects are timezone-aware
            if event_data['start_time'].tzinfo is None:
                event_data_copy['start_time'] = pytz.timezone(self.timezone).localize(event_data['start_time'])
            if event_data['end_time'].tzinfo is None:
                event_data_copy['end_time'] = pytz.timezone(self.timezone).localize(event_data['end_time'])
            
            event_data_copy['start_time'] = event_data_copy['start_time'].isoformat()
            event_data_copy['end_time'] = event_data_copy['end_time'].isoformat()
            
            c.execute('''
                INSERT OR REPLACE INTO events 
                (uuid, title, start_time, end_time, location, description, url, calendar_url, location_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                event_uuid,
                event_data_copy['title'],
                event_data_copy['start_time'],
                event_data_copy['end_time'],
                event_data_copy.get('location', 'Online'),
                event_data_copy.get('description', ''),
                event_data_copy['url'],
                event_data_copy.get('calendar_url'),
                event_data_copy.get('location_url')
            ))
            conn.commit()
            conn.close()
            logger.info(f"Saved event {event_uuid} to database")
        except Exception as e:
            logger.error(f"Error saving event data to database: {e}")
            logger.error(traceback.format_exc())

    def delete_event_data(self, event_uuid):
        """Delete event data from SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('DELETE FROM events WHERE uuid = ?', (event_uuid,))
            conn.commit()
            conn.close()
            logger.info(f"Deleted event {event_uuid} from database")
        except Exception as e:
            logger.error(f"Error deleting event data from database: {e}")
            logger.error(traceback.format_exc())

    def load_meetup_configs(self) -> List[MeetupConfig]:
        return [MeetupConfig(cfg) for cfg in CONFIG['meetup_configs']['locations']]

    async def setup_hook(self):
        """Set up the bot's initial state."""
        # Register views for all loaded events
        for event_uuid, event_data in self.event_data_map.items():
            logger.info(f"Registering view for event: {event_uuid}")
            self.add_view(CreateEventView(event_uuid, event_data.get('calendar_url', ''), event_data.get('location_url', '')))
        
        # Start the weekly meetup task as a background task
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

    async def on_interaction(self, interaction: discord.Interaction):
        """Log all interactions"""
        logger.info(f"Received interaction: {interaction.type}")
        logger.info(f"Interaction data: {interaction.data}")
        if interaction.type == discord.InteractionType.component:
            logger.info(f"Component ID: {interaction.data.get('custom_id')}")
            logger.info(f"Component type: {interaction.data.get('component_type')}")
            logger.info(f"User: {interaction.user.name} (ID: {interaction.user.id})")
            logger.info(f"Message ID: {interaction.message.id}")
            logger.info(f"Channel: {interaction.channel.name} (ID: {interaction.channel.id})")
            logger.info(f"Guild: {interaction.guild.name} (ID: {interaction.guild.id})")
            # Let the view handle the interaction
            await self.process_application_commands(interaction)

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

        # Only keep future events
        tz = pytz.timezone(self.timezone)
        now = datetime.now(tz)
        future_events = [event for event in unique_events if event['time'] > now]
        logger.info(f"Filtered to {len(future_events)} future events")

        # Sort events by date
        sorted_events = sorted(future_events, key=lambda x: x['time'])

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
                    # Check if this event already exists in the database
                    conn = sqlite3.connect(self.db_path)
                    c = conn.cursor()
                    c.execute('SELECT uuid FROM events WHERE title = ? AND start_time = ?', 
                             (message_data['event_data']['title'], 
                              message_data['event_data']['start_time'].isoformat()))
                    result = c.fetchone()
                    conn.close()

                    if result:
                        # Use existing UUID
                        event_uuid = result[0]
                    else:
                        # Generate new UUID
                        event_uuid = str(uuid.uuid4())
                        # Store event data in the mapping
                        self.event_data_map[event_uuid] = message_data['event_data']
                        # Save event data to database
                        self.save_event_data(event_uuid, message_data['event_data'])

                    view = CreateEventView(event_uuid, message_data['calendar_url'], message_data['location_url'], location_text=message_data['location_text'])
                    await self.channel.send(message_data['message'], view=view)
                    logger.info(f"Posted event: {event['title']}")
                    await asyncio.sleep(2)
                except discord.errors.HTTPException as e:
                    logger.error(f"Error posting event to Discord: {e}")
                    continue

        logger.info("Finished posting all events to Discord")

    async def post_event(self, event, channel):
        """Post a single event to Discord"""
        try:
            # Generate UUID for this event
            event_uuid = str(uuid.uuid4())
            
            # Format the event data for storage
            formatted_event = {
                'title': event['title'],
                'start_time': event['time'],
                'end_time': event['time'] + timedelta(hours=1),  # Default 1-hour duration
                'location': event['location']['name'] if isinstance(event['location'], dict) else event['location'],
                'description': event.get('description', ''),
                'url': event['url'],
                'calendar_url': f"https://calendar.google.com/calendar/render?action=TEMPLATE&text={urllib.parse.quote(event['title'])}&dates={event['time'].strftime('%Y%m%dT%H%M%S')}/{event['time'].strftime('%Y%m%dT%H%M%S')}&details={urllib.parse.quote(event['url'])}",
                'location_url': None
            }
            
            # Store event data in memory and database
            self.event_data_map[event_uuid] = formatted_event
            self.save_event_data(event_uuid, formatted_event)
            
            # Create buttons with UUID
            view = discord.ui.View()
            view.add_item(CreateEventButton(event_uuid, formatted_event['calendar_url'], formatted_event['location_url']))
            view.add_item(CalendarButton(formatted_event['calendar_url']))
            if formatted_event['location_url']:
                view.add_item(LocationButton(formatted_event['location_url']))
            
            # Format and post the event message
            content = format_event_message(event)
            await channel.send(content=content, view=view)
            logger.info(f"Posted event: {event['title']}")
            
        except Exception as e:
            logger.error(f"Error posting event: {e}")
            logger.error(traceback.format_exc())

    async def send_meetup_message(self, event_uuid: str, event_data: dict):
        """Send a message about a new meetup event."""
        try:
            # Get the channel
            channel = self.get_channel(self.discord_channel_id)
            if not channel:
                logger.error(f"Could not find channel {self.discord_channel_id}")
                return

            # Create the message
            message = (
                f"🎉 **New Meetup Event!**\n\n"
                f"**{event_data['title']}**\n"
                f"📅 {event_data['date']}\n"
                f"⏰ {event_data['time']}\n"
                f"📍 {event_data['location']}\n"
                f"🔗 [View on Meetup]({event_data['url']})"
            )

            # Send the message with the button
            view = CreateEventView(event_uuid, event_data.get('calendar_url', ''), event_data.get('location_url', ''), location_text=event_data.get('location', ''))
            await channel.send(content=message, view=view)
            logger.info(f"Sent meetup message for event {event_uuid}")

        except Exception as e:
            logger.error(f"Error sending meetup message: {str(e)}")
            logger.error(traceback.format_exc())

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
    
    # Create the Google Calendar URL with truncated title
    calendar_title = title[:100]  # Truncate title to avoid URL length issues
    calendar_url = (
        f"https://calendar.google.com/calendar/render"
        f"?action=TEMPLATE"
        f"&text={urllib.parse.quote(calendar_title)}"
        f"&dates={start_date}/{end_date}"
        f"&details={urllib.parse.quote(event['url'])}"
    )
    
    # Add location to calendar URL if available, but keep URL under 512 characters
    if location_text and str(location_text).strip():
        location_text_truncated = str(location_text)[:100]  # Truncate location text
        calendar_url += f"&location={urllib.parse.quote(location_text_truncated)}"
        location_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(location_text_truncated)}"
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

    # Add online indicator if it's an online event
    online_text = " | ☎️ Online" if event.get('eventAttendanceMode') == 'online' else ""

    # Create the message
    message = (
        f"# {clean_title}\n"
        f"**{event['group']}** | **{date_text}**{online_text}\n"
        f"\n"  # Add an extra empty line after the date
    )
    
    # Return both the message and the event data needed for the button
    return {
        'message': message,
        'event_data': {
            'title': title,  # Remove [Meetup] prefix here
            'start_time': event_time,
            'end_time': event_time + timedelta(hours=1),
            'location': location_text if location_text and str(location_text).strip() else "Online",
            'description': description,
            'url': event['url']
        },
        'calendar_url': calendar_url,
        'location_url': location_url,
        'location_text': location_text
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