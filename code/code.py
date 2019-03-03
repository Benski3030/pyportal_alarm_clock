"""
PyPortal based alarm clock.

Adafruit invests time and resources providing this open source code.
Please support Adafruit and open source hardware by purchasing
products from Adafruit!

Written by Dave Astels for Adafruit Industries
Copyright (c) 2019 Adafruit Industries
Licensed under the MIT license.

All text above must be included in any redistribution.
"""

#pylint:disable=redefined-outer-name,no-member,global-statement

import time
import json
import board
from adafruit_pyportal import PyPortal
from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.text_area import TextArea
from digitalio import DigitalInOut, Direction, Pull
import analogio
import displayio
from secrets import secrets

# Set up where we'll be fetching data from
DATA_SOURCE = 'http://api.openweathermap.org/data/2.5/weather?q='+secrets['weather_location']
DATA_SOURCE += '&appid='+secrets['openweather_token']
# You'll need to get a token from openweather.org, looks like 'b6907d289e10d714a6e88b30761fae22'
DATA_LOCATION = []

####################
# setup hardware

pyportal = PyPortal(url=DATA_SOURCE,
                    json_path=DATA_LOCATION,
                    status_neopixel=board.NEOPIXEL)

light = analogio.AnalogIn(board.LIGHT)

snooze_button = DigitalInOut(board.D3)
snooze_button.direction = Direction.INPUT
snooze_button.pull = Pull.UP

####################
# variables

# alarm support

alarm_background = 'red_alert.bmp'
alarm_file = 'computer-alert20.wav'
alarm_enabled = True
alarm_armed = True
alarm_interval = 5.0
alarm_hour = 23
alarm_minute = 20
snooze_time = None
snooze_interval = 600.0

# weather support

icon_file = None
icon_sprite = None
celcius = secrets['celcius']

# display/data refresh timers

refresh_time = None
update_time = None
weather_refresh = None

# track whether we're in low light mode

low_light = False

####################
# Functions

def load_fonts():
    """Create, pre-fetch, and return small, medium, and large fonts.
    These are used for temperature, alarm, and time, respectively"""

    large_font_name = '/fonts/Anton-Regular-104.bdf'
    medium_font_name = '/fonts/Helvetica-Bold-36.bdf'
    small_font_name = '/fonts/Arial-16.bdf'

    large_font = bitmap_font.load_font(large_font_name)
    large_font.load_glyphs(b'0123456789:') # pre-load glyphs for fast printing

    medium_font = bitmap_font.load_font(medium_font_name)
    medium_font.load_glyphs(b'0123456789:')

    small_font = bitmap_font.load_font(small_font_name)
    small_font.load_glyphs(b'0123456789CF')

    return small_font, medium_font, large_font

small_font, medium_font, large_font = load_fonts()

def create_text_areas(configs):
    """Given a list of area specifications, create and return test areas."""
    text_areas = []
    for cfg in configs:
        textarea = TextArea(cfg['font'], text=' '*cfg['size'])
        textarea.x = cfg['x']
        textarea.y = cfg['y']
        textarea.color = cfg['color']
        text_areas.append(textarea)
    return text_areas


def clear_splash():
    for _ in range(len(pyportal.splash) - 1):
        pyportal.splash.pop()


def touch_in_button(t, b):
    return (b['left'] >= t[0] >= b['right']) and (b['top'] <= t[1] <= b['bottom'])


touched = False

####################
# states

class State(object):
    """State abstract base class"""

    def __init__(self):
        pass


    @property
    def name(self):
        """Return the name of teh state"""
        return ''


    def tick(self, now):
        """Handle a tick: one pass through the main loop"""
        pass


    #pylint:disable=unused-argument
    def touch(self, t, touched):
        """Handle a touch event.
        :param (x, y, z) - t: the touch location/strength"""
        return bool(t)


    def enter(self):
        """Just after the state is entered."""
        pass


    def exit(self):
        """Just before the state exits."""
        clear_splash()


class Time_State(State):
    """This state manages the primary time display screen/mode"""

    def __init__(self):
        super().__init__()
        self.background_day = 'main_background_day.bmp'
        self.background_night = 'main_background_night.bmp'
        self.refresh_time = None
        self.update_time = None
        self.weather_refresh = None
        text_area_configs = [dict(x=88, y=30, size=5, color=0xFFFFFF, font=large_font),
                             dict(x=210, y=10, size=5, color=0xFF0000, font=medium_font),
                             dict(x=88, y=65, size=6, color=0xFFFFFF, font=small_font)]
        self.text_areas = create_text_areas(text_area_configs)
        self.weather_icon = displayio.Group(max_size=1)
        self.weather_icon.x = 88
        self.weather_icon.y = 20
        self.icon_file = None

        self.snooze_icon = displayio.Group(max_size=1)
        self.snooze_icon.x = 270
        self.snooze_icon.y = 58
        self.snooze_file = None

        # each button has it's edges as well as the state to transition to when touched
        self.buttons = [dict(left=320, top=50, right=240, bottom=120, next_state='settings'),
                        dict(left=320, top=155, right=240, bottom=220, next_state='mugsy')]


    @property
    def name(self):
        return 'time'


    def adjust_backlight_based_on_light(self, force=False):
        """Check light level. Adjust the backlight and background image if it's dark."""
        global low_light
        if light.value <= 1000 and (force or not low_light):
            pyportal.set_backlight(0.01)
            pyportal.set_background(self.background_night)
            low_light = True
        elif force or (light.value >= 2000 and low_light):
            pyportal.set_backlight(1.00)
            pyportal.set_background(self.background_day)
            low_light = False


    def tick(self, now):
        global alarm_armed, snooze_time, update_time

        # is the snooze button pushed? Cancel the snooze if so.
        if not snooze_button.value:
            if snooze_time:
                self.snooze_icon.pop()
            snooze_time = None
            alarm_armed = False

        # is snooze active and the snooze time has passed? Transition to alram is so.
        if snooze_time and ((now - snooze_time) >= snooze_interval):
            change_to_state('alarm')
            return

        # check light level and adjust background & backlight
        self.adjust_backlight_based_on_light()

        # only query the online time once per hour (and on first run)
        if (not self.refresh_time) or ((now - self.refresh_time) > 3600):
            try:
                pyportal.get_local_time(location=secrets['time_location'])
                self.refresh_time = now
            except RuntimeError as e:
                print('Some error occured, retrying! -', e)

        # only query the weather every 10 minutes (and on first run)
        if (not self.weather_refresh) or (now - self.weather_refresh) > 600:
            try:
                value = pyportal.fetch()
                weather = json.loads(value)

                # set the icon/background
                weather_icon_name = weather['weather'][0]['icon']
                try:
                    self.weather_icon.pop()
                except IndexError:
                    pass
                filename = "/icons/"+weather_icon_name+".bmp"
                if filename:
                    if self.icon_file:
                        self.icon_file.close()
                    self.icon_file = open(filename, "rb")
                    icon = displayio.OnDiskBitmap(self.icon_file)
                    icon_sprite = displayio.TileGrid(icon,
                                                     pixel_shader=displayio.ColorConverter(),
                                                     position=(0, 0))

                    self.weather_icon.append(icon_sprite)

                temperature = weather['main']['temp'] - 273.15 # its...in kelvin
                if celcius:
                    temperature_text = '%3d C' % round(temperature)
                else:
                    temperature_text = '%3d F' % round(((temperature * 9 / 5) + 32))
                self.text_areas[2].text = temperature_text
                self.weather_refresh = now
                board.DISPLAY.refresh_soon()
                board.DISPLAY.wait_for_frame()

            except RuntimeError as e:
                print("Some error occured, retrying! -", e)

        if (not update_time) or ((now - update_time) > 30):
            # Update the time
            update_time = now
            the_time = time.localtime()
            self.text_areas[0].text = '%02d:%02d' % (the_time.tm_hour,the_time.tm_min) # set time textarea
            board.DISPLAY.refresh_soon()
            board.DISPLAY.wait_for_frame()

            # Check if alarm should sound
            if not snooze_time:
                minutes_now = the_time.tm_hour * 60 + the_time.tm_min
                minutes_alarm = alarm_hour * 60 + alarm_minute
                if minutes_now == minutes_alarm:
                    if alarm_armed:
                        change_to_state('alarm')
                else:
                    alarm_armed = alarm_enabled


    def touch(self, t, touched):
        if t and not touched:             # only process the initial touch
            for button_index in range(len(self.buttons)):
                b = self.buttons[button_index]
                if touch_in_button(t, b):
                    change_to_state(b['next_state'])
                    break
        return bool(t)


    def enter(self):
        self.adjust_backlight_based_on_light(force=True)
        for ta in self.text_areas:
            pyportal.splash.append(ta)
        pyportal.splash.append(self.weather_icon)
        if snooze_time:
            if self.snooze_file:
                self.snooze_file.close()
            self.snooze_file = open('/icons/zzz.bmp', "rb")
            icon = displayio.OnDiskBitmap(self.snooze_file)
            icon_sprite = displayio.TileGrid(icon,
                                             pixel_shader=displayio.ColorConverter(),
                                             position=(0, 0))
            self.snooze_icon.append(icon_sprite)

        pyportal.splash.append(self.snooze_icon)
        if alarm_enabled:
            self.text_areas[1].text = '%2d:%02d' % (alarm_hour, alarm_minute)
        else:
            self.text_areas[1].text = '     '
        board.DISPLAY.refresh_soon()
        board.DISPLAY.wait_for_frame()



class Mugsy_State(Time_State):
    """This state tells Mugsey 'Make me a coffee' """

    def __init__(self):
        super().__init__()


    @property
    def name(self):
        return 'mugsy'


    def tick(self, now):
        # Once the job is done, go back to the main screen
        change_to_state('time')


class Alarm_State(State):
    """This state shows/sounds the alarm.
    Touching anywhere on the screen cancells the alarm.
    Pressing the snooze button turns of the alarm, starting it again in 10 minutes."""

    def __init__(self):
        super().__init__()
        self.sound_alarm_time = None


    @property
    def name(self):
        return 'alarm'


    def tick(self, now):
        global snooze_time

        # is the snooze button pushed
        if not snooze_button.value:
            snooze_time = now
            change_to_state('time')
            return

        # is it time to sound the alarm?
        if self.sound_alarm_time and (now - self.sound_alarm_time) > alarm_interval:
            self.sound_alarm_time = now
            pyportal.play_file(alarm_file)


    def touch(self, t, touched):
        global snooze_time
        if t and not touched:
            snooze_time = None
            change_to_state('time')
        return bool(t)


    def enter(self):
        global low_light
        self.sound_alarm_time = time.monotonic()
        pyportal.set_backlight(1.00)
        pyportal.set_background(alarm_background)
        low_light = False
        board.DISPLAY.refresh_soon()
        board.DISPLAY.wait_for_frame()


    def exit(self):
        global alarm_armed
        super().exit()
        alarm_armed = bool(snooze_time)


class Setting_State(State):
    """This state lets the user enable/disable the alarm and set its time.
    Swiping up/down adjusts the hours & miniutes separately."""

    def __init__(self):
        super().__init__()
        self.previous_touch = None
        self.background = 'settings_background.bmp'
        text_area_configs = [dict(x=88, y=-10, size=5, color=0xFFFFFF, font=large_font)]

        self.text_areas = create_text_areas(text_area_configs)
        self.buttons = [dict(left=320, top=30, right=240, bottom=93),    # on
                        dict(left=320, top=98, right=240, bottom=152),   # return
                        dict(left=320, top=155, right=240, bottom=220),  # off
                        dict(left=240, top=0, right=120, bottom = 240), # hours
                        dict(left=120, top=0, right=0, bottom = 240)]   # minutes


    @property
    def name(self):
        return 'settings'


    def touch(self, t, touched):
        global alarm_hour, alarm_minute, alarm_enabled
        if t:
            if touch_in_button(t, self.buttons[0]):   # on
                alarm_enabled = True
                self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
            elif touch_in_button(t, self.buttons[1]):   # return
                change_to_state('time')
            elif touch_in_button(t, self.buttons[2]): # off
                alarm_enabled = False
                self.text_areas[0].text = '     '
            elif alarm_enabled:
                if not self.previous_touch:
                    self.previous_touch = t
                else:
                    if touch_in_button(t, self.buttons[3]):   # HOURS
                        if t[1] < (self.previous_touch[1]):   # moving up
                            alarm_hour = (alarm_hour + 1) % 24
                        elif t[1] > (self.previous_touch[1]): # moving down
                            alarm_hour = (alarm_hour - 1) % 24
                        self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
                    elif touch_in_button(t, self.buttons[4]): # MINUTES
                        if t[1] < (self.previous_touch[1]):   # moving up
                            alarm_minute = (alarm_minute + 1) % 60
                        elif t[1] > (self.previous_touch[1]): # moving down
                            alarm_minute = (alarm_minute - 1) % 60
                        self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute)
            board.DISPLAY.refresh_soon()
            board.DISPLAY.wait_for_frame()
        else:
            self.previous_touch = None
        return bool(t)


    def enter(self):
        pyportal.set_background(self.background)
        for ta in self.text_areas:
            pyportal.splash.append(ta)
        if alarm_enabled:
            self.text_areas[0].text = '%02d:%02d' % (alarm_hour, alarm_minute) # set time textarea
        else:
            self.text_areas[0].text = '     '


####################
# State management

states = {'time': Time_State(),
          'mugsy': Mugsy_State(),
          'alarm': Alarm_State(),
          'settings': Setting_State()}

current_state = None


def change_to_state(state_name):
    global current_state
    if current_state:
        current_state.exit()
    current_state = states[state_name]
    current_state.enter()

####################
# And... go

clear_splash()
change_to_state("time")

while True:
    touched = current_state.touch(pyportal.touchscreen.touch_point, touched)
    current_state.tick(time.monotonic())
