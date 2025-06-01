#!/bin/bash

# Install the systemd service for the Meetup Discord Bot
sudo cp meetup-bot.service /etc/systemd/system/meetup-bot.service
sudo systemctl daemon-reload
sudo systemctl enable meetup-bot
sudo systemctl start meetup-bot 