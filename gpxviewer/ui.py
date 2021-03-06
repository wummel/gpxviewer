#
#  ui.py - GUI for GPX Viewer
#
#  Copyright (C) 2009 Andrew Gee
#
#  GPX Viewer is free software: you can redistribute it and/or modify it
#  under the terms of the GNU General Public License as published by the
#  Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#  
#  GPX Viewer is distributed in the hope that it will be useful, but
#  WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#  See the GNU General Public License for more details.
#  
#  You should have received a copy of the GNU General Public License along
#  with this program.  If not, see <http://www.gnu.org/licenses/>.

#
#  If you're having any problems, don't hesitate to contact: andrew@andrewgee.org
#
import os

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('OsmGpsMap', '1.0')

from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject

from gi.repository import OsmGpsMap

from . import stats

from .gpx import GPXTrace

from .utils.timezone import LocalTimezone

import locale
import gettext

locale.setlocale(locale.LC_ALL, '')
gettext.bindtextdomain('gpxviewer')
gettext.textdomain('gpxviewer')
_ = gettext.gettext


# Function used to defer translation until later, while still being recognised
# by build_i18n
def N_(message):
    return message


def show_url(url):
    Gtk.show_uri(None, url, Gdk.CURRENT_TIME)


ALPHA_UNSELECTED = 0.5
ALPHA_SELECTED = 0.8
LAZY_LOAD_AFTER_N_FILES = 3


class _TrackManager(GObject.GObject):
    NAME_IDX = 0
    FILENAME_IDX = 1

    __gsignals__ = {
        'track-added': (GObject.SIGNAL_RUN_LAST, GObject.TYPE_NONE, [object, object]),
        'track-removed': (GObject.SIGNAL_RUN_LAST, GObject.TYPE_NONE, [object, object]),
    }

    def __init__(self):
        GObject.GObject.__init__(self)
        # maps track_filename : (GPXTrace, [OsmGpsMapTrack])
        self._tracks = {}
        # name, filename
        self.model = Gtk.ListStore(str, str)

    def get_other_tracks(self, trace):
        tracks = []
        for _trace, _tracks in self._tracks.values():
            if trace != _trace:
                tracks += _tracks
        return tracks

    def get_trace_from_model(self, _iter):
        filename = self.model.get_value(_iter, self.FILENAME_IDX)
        return self.get_trace(filename)

    def delete_trace_from_model(self, _iter):
        self.emit("track-removed", *self._tracks[self.model.get_value(_iter, self.FILENAME_IDX)])
        self.model.remove(_iter)

    def get_trace(self, filename):
        """ Returns (trace, [OsmGpsMapTrack]) """
        return self._tracks[filename]

    def add_trace(self, trace):
        filename = trace.get_full_path()
        if filename not in self._tracks:
            gpstracks = []
            for track in trace.get_points():
                for segment in track:

                    gpstrack = OsmGpsMap.MapTrack()
                    gpstrack.props.alpha = 0.8

                    for rlat, rlon in segment:
                        gpstrack.add_point(OsmGpsMap.MapPoint.new_radians(rlat, rlon))
                    gpstracks.append(gpstrack)

            self._tracks[filename] = (trace, gpstracks)
            self.model.append((trace.get_display_name(), filename))
            self.emit("track-added", trace, gpstracks)

    def num_traces(self):
        return len(self._tracks)

    def get_all_traces(self):
        return [t[0] for t in self._tracks.values()]


class MainWindow:
    def __init__(self, ui_dir, files):
        self.local_tz = LocalTimezone()
        self.recent = Gtk.RecentManager.get_default()

        self.wTree = Gtk.Builder()
        self.wTree.set_translation_domain('gpxviewer')
        self.wTree.add_from_file("%sgpxviewer.ui" % ui_dir)

        signals = {
            "on_windowMain_destroy": self.quit,
            "on_menuitemQuit_activate": self.quit,
            "on_menuitemOpen_activate": self.open_gpx,
            "on_menuitemZoomIn_activate": self.zoom_map_in,
            "on_buttonZoomIn_clicked": self.zoom_map_in,
            "on_menuitemZoomOut_activate": self.zoom_map_out,
            "on_buttonZoomOut_clicked": self.zoom_map_out,
            "on_menuitemAbout_activate": self.open_about_dialog,
            "on_checkmenuitemShowSidebar_toggled": self.show_sidebar_toggled,
            "on_menuitemShowStatistics_activate": self.show_statistics,
            "on_buttonTrackAdd_clicked": self.button_track_add_clicked,
            "on_buttonTrackDelete_clicked": self.button_track_delete_clicked,
            "on_buttonTrackProperties_clicked": self.button_track_properties_clicked,
            "on_buttonTrackInspect_clicked": self.button_track_inspect_clicked,
        }

        self.mainWindow = self.wTree.get_object("windowMain")
        self.mainWindow.set_icon_from_file("%sgpxviewer.svg" % ui_dir)
        self.mainWindow.set_title(_("GPX Viewer"))

        i = self.wTree.get_object("checkmenuitemCenter")
        i.connect("toggled", self.auto_center_toggled)
        self.autoCenter = i.get_active()

        self.ui_dir = ui_dir

        self.map = OsmGpsMap.Map(
            tile_cache=os.path.join(
                GLib.get_user_cache_dir(),
                'gpxviewer', 'tiles'))
        self.map.layer_add(
            OsmGpsMap.MapOsd(
                show_dpad=False,
                show_zoom=False,
                show_scale=True,
                show_coordinates=False))
        self.wTree.get_object("hbox_map").pack_start(self.map, True, True, 0)

        sb = self.wTree.get_object("statusbar1")
        # move zoom control into apple like slider
        self.zoomSlider = MapZoomSlider(self.map)
        self.zoomSlider.show_all()
        a = Gtk.Alignment.new(1.0, 1.0, 0.0, 0.0)
        a.set_padding(0, 0, 0, 4)
        a.add(self.zoomSlider)
        a.show_all()
        overlay = self.wTree.get_object("overlay_map")
        overlay.add_overlay(a)
        # pass-through clicks to the map widget
        overlay.set_overlay_pass_through(a, True)

        # animate a spinner when downloading tiles
        try:
            self.spinner = Gtk.Spinner()
            self.spinner.props.has_tooltip = True
            self.spinner.connect("query-tooltip", self.on_spinner_tooltip)
            self.map.connect("notify::tiles-queued", self.update_tiles_queued)
            self.spinner.set_size_request(*Gtk.icon_size_lookup(Gtk.IconSize.MENU)[:2])
            sb.pack_end(self.spinner, False, False, 0)
        except AttributeError:
            self.spinner = None

        self.wTree.connect_signals(signals)

        # add open with external tool submenu items and actions
        programs = {
            'josm': N_('JOSM Editor'),
            'merkaartor': N_('Merkaartor'),
        }
        submenu_open_with = Gtk.Menu()
        for prog, progname in programs.items():
            submenuitem_open_with = Gtk.MenuItem(_(progname))
            submenu_open_with.append(submenuitem_open_with)
            submenuitem_open_with.connect("activate", self.open_with_external_app, prog)
            submenuitem_open_with.show()

        self.wTree.get_object('menuitemOpenBy').set_submenu(submenu_open_with)

        self.trackManager = _TrackManager()
        self.trackManager.connect("track-added", self.on_track_added)
        self.trackManager.connect("track-removed", self.on_track_removed)

        self.wTree.get_object("menuitemHelp").connect("activate",
                                                      lambda *a: show_url("https://answers.launchpad.net/gpxviewer"))
        self.wTree.get_object("menuitemTranslate").connect("activate", lambda *a: show_url(
            "https://translations.launchpad.net/gpxviewer"))
        self.wTree.get_object("menuitemReportProblem").connect("activate", lambda *a: show_url(
            "https://bugs.launchpad.net/gpxviewer/+filebug"))

        self.tv = Gtk.TreeView(self.trackManager.model)
        self.tv.get_selection().connect("changed", self.on_selection_changed)
        self.tv.append_column(
            Gtk.TreeViewColumn(
                "Track Name",
                Gtk.CellRendererText(),
                text=self.trackManager.NAME_IDX
            )
        )
        self.wTree.get_object("scrolledwindow1").add(self.tv)
        self.sb = self.wTree.get_object("vbox_sidebar")

        self.hide_spinner()
        self.hide_track_selector()

        self.lazyLoadFiles(files)

        self.map.show()
        self.mainWindow.show()

    def lazyLoadFiles(self, files):
        def do_lazy_load(_files):
            try:
                self.load_gpx(_files.pop())
                self.loadingFiles -= 1
                return True
            except IndexError:
                self.loadingFiles = 0
                return False

        self.loadingFiles = 0
        if not files:
            return

        # if less than LAZY_LOAD_AFTER_N_FILES load directly, else
        # load on idle
        if len(files) < LAZY_LOAD_AFTER_N_FILES:
            i = 0
            for filename in files:
                self.loadingFiles = i
                trace = self.load_gpx(filename)
                if i < LAZY_LOAD_AFTER_N_FILES:
                    i += 1
                else:
                    # select the last loaded trace
                    self.loadingFiles = 0
                    self.select_trace(trace)
                    break
        else:
            self.loadingFiles = len(files)
            GObject.timeout_add(100, do_lazy_load, files)

    def show_spinner(self):
        if self.spinner:
            self.spinner.show()
            self.spinner.start()

    def hide_spinner(self):
        if self.spinner:
            self.spinner.stop()
            self.spinner.hide()

    def on_spinner_tooltip(self, spinner, x, y, keyboard_mode, tooltip):
        tiles = self.map.props.tiles_queued
        if tiles:
            tooltip.set_text("Downloading Map")
            return True
        return False

    def show_track_selector(self):
        self.sb.show_all()

    def hide_track_selector(self):
        self.sb.hide()

    def on_selection_changed(self, selection):
        model, _iter = selection.get_selected()
        if not _iter:
            return

        trace, tracks = self.trackManager.get_trace_from_model(_iter)
        self.select_trace(trace)

        # highlight current track
        self.select_tracks(tracks, ALPHA_SELECTED)
        # dim other tracks
        self.select_tracks(self.trackManager.get_other_tracks(trace), ALPHA_UNSELECTED)

    def on_track_added(self, tm, trace, tracks):
        for t in tracks:
            self.map.track_add(t)
        self.select_trace(trace)

    def on_track_removed(self, tm, trace, tracks):
        for t in tracks:
            self.map.track_remove(t)

    def update_tiles_queued(self, map_, paramspec):
        if self.map.props.tiles_queued > 0:
            self.show_spinner()
        else:
            self.hide_spinner()

    def show_sidebar_toggled(self, item):
        if item.get_active():
            self.show_track_selector()
        else:
            self.hide_track_selector()

    def show_statistics(self, item):
        ws = stats.WeekStats()
        ss = stats.AvgSpeedStats()
        for t in self.trackManager.get_all_traces():
            ws.addTrace(t)
            ss.addTrace(t)

        w = Gtk.Window()
        w.add(stats.ChartNotebook(ws, ss))
        w.resize(500, 300)
        w.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        w.set_transient_for(self.mainWindow)
        w.show_all()

    def open_about_dialog(self, w):
        dialog = self.wTree.get_object("dialogAbout")
        self.wTree.get_object("dialogAbout").set_icon_from_file("%sgpxviewer.svg" % self.ui_dir)
        dialog.connect("response", lambda *a: dialog.hide())
        dialog.show_all()

    def select_tracks(self, tracks, alpha):
        for t in tracks:
            t.props.alpha = alpha

    def select_trace(self, trace):
        if self.loadingFiles:
            return

        self.zoom = 12
        distance = trace.get_distance()
        maximum_speed = trace.get_maximum_speed()
        average_speed = trace.get_average_speed()
        duration = trace.get_duration()
        clat, clon = trace.get_centre()
        gpxfrom = trace.get_gpxfrom().astimezone(self.local_tz)
        gpxto = trace.get_gpxto().astimezone(self.local_tz)

        self.set_distance_label(round(distance / 1000, 2))
        self.set_maximum_speed_label(maximum_speed)
        self.set_average_speed_label(average_speed)
        self.set_duration_label(int(duration / 60), duration - (int(duration / 60) * 60))
        self.set_logging_date_label(gpxfrom.strftime("%x"))
        self.set_logging_time_label(gpxfrom.strftime("%X"), gpxto.strftime("%X"))

        self.currentFilename = trace.get_filename()
        self.mainWindow.set_title(_("GPX Viewer - %s") % trace.get_filename())

        if self.autoCenter:
            self.set_centre(clat, clon)

    def load_gpx(self, filename):
        try:
            trace = GPXTrace(filename)
            self.trackManager.add_trace(trace)
            if self.trackManager.num_traces() > 1:
                self.show_track_selector()
            return trace
        except Exception as e:
            self.show_gpx_error()
            return None

    def open_gpx(self, *args):
        filechooser = Gtk.FileChooserDialog(title=_("Choose a GPX file to Load"), action=Gtk.FileChooserAction.OPEN,
                                            parent=self.mainWindow)
        filechooser.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.DELETE_EVENT)
        filechooser.add_button(Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filechooser.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        filechooser.set_select_multiple(True)
        response = filechooser.run()

        if response == Gtk.ResponseType.OK:
            for filename in filechooser.get_filenames():
                if self.load_gpx(filename):
                    self.recent.add_item("file://" + filename)

        filechooser.destroy()

    def show_gpx_error(self):
        message_box = Gtk.MessageDialog(parent=self.mainWindow, type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK,
                                        message_format=_("You selected an invalid GPX file. \n Please try again"))
        message_box.run()
        message_box.destroy()
        return None

    def quit(self, w):
        Gtk.main_quit()

    def main(self):
        Gtk.main()

    def open_with_external_app(self, w, app):
        if self.currentFilename:
            os.spawnlp(os.P_NOWAIT, app, app, self.currentFilename)

    def zoom_map_in(self, w):
        self.map.zoom_in()

    def zoom_map_out(self, w):
        self.map.zoom_out()

    def set_centre(self, lat, lon):
        self.map.set_center_and_zoom(lat, lon, self.zoom)

    def set_distance_label(self, distance="--"):
        self.wTree.get_object("labelDistance").set_markup(_("<b>Distance:</b> %.2f km") % distance)

    def set_average_speed_label(self, average_speed="--"):
        self.wTree.get_object("labelAverageSpeed").set_markup(_("<b>Average Speed:</b> %.2f m/s") % average_speed)

    def set_maximum_speed_label(self, maximum_speed="--"):
        self.wTree.get_object("labelMaximumSpeed").set_markup(_("<b>Maximum Speed:</b> %.2f m/s") % maximum_speed)

    def set_duration_label(self, minutes="--", seconds="--"):
        self.wTree.get_object("labelDuration").set_markup(
            _("<b>Duration:</b> %(minutes)s minutes, %(seconds)s seconds") % {"minutes": minutes, "seconds": seconds})

    def set_logging_date_label(self, gpxdate="--"):
        self.wTree.get_object("labelLoggingDate").set_markup(_("<b>Logging Date:</b> %s") % gpxdate)

    def set_logging_time_label(self, gpxfrom="--", gpxto="--"):
        self.wTree.get_object("labelLoggingTime").set_markup(
            _("<b>Logging Time:</b> %(from)s - %(to)s") % {"from": gpxfrom, "to": gpxto})

    def auto_center_toggled(self, item):
        self.autoCenter = item.get_active()

    def button_track_add_clicked(self, *args):
        self.open_gpx()

    def button_track_delete_clicked(self, *args):
        model, _iter = self.tv.get_selection().get_selected()
        if _iter:
            self.trackManager.delete_trace_from_model(_iter)

    def button_track_properties_clicked(self, *args):
        model, _iter = self.tv.get_selection().get_selected()
        if _iter:
            trace, OsmGpsMapTracks = self.trackManager.get_trace_from_model(_iter)
            colorseldlg = Gtk.ColorSelectionDialog("Select track color")
            colorseldlg.get_color_selection().set_current_color(OsmGpsMapTracks[0].props.color)
            result = colorseldlg.run()
            if result == Gtk.ResponseType.OK:
                color = colorseldlg.get_color_selection().get_current_rgba()
                for OsmGpsMapTrack in OsmGpsMapTracks:
                    OsmGpsMapTrack.set_color(color)
                    self.map.map_redraw()
            colorseldlg.destroy()

    def button_track_inspect_clicked(self, *args):
        pass


class MapZoomSlider(Gtk.HBox):
    def __init__(self, _map):
        Gtk.HBox.__init__(self)

        zo = Gtk.EventBox()
        zo.add(Gtk.Image.new_from_stock(Gtk.STOCK_ZOOM_OUT, Gtk.IconSize.MENU))
        zo.connect("button-press-event", self._on_zoom_out_pressed, _map)
        self.pack_start(zo, False, False, 0)

        self.zoom = Gtk.Adjustment(
            value=_map.props.zoom,
            lower=_map.props.min_zoom,
            upper=_map.props.max_zoom,
            step_incr=1,
            page_incr=1,
            page_size=0)
        self.zoom.connect("value-changed", self._on_zoom_slider_value_changed, _map)
        hs = Gtk.HScale()
        hs.set_adjustment(self.zoom)
        hs.props.digits = 0
        hs.props.draw_value = False
        hs.set_size_request(100, -1)
        # hs.set_update_policy(gtk.UPDATE_DISCONTINUOUS)
        self.pack_start(hs, True, True, 0)

        zi = Gtk.EventBox()
        zi.add(Gtk.Image.new_from_stock(Gtk.STOCK_ZOOM_IN, Gtk.IconSize.MENU))
        zi.connect("button-press-event", self._on_zoom_in_pressed, _map)
        self.pack_start(zi, False, False, 0)

        _map.connect("notify::zoom", self._on_map_zoom_changed)

    def _on_zoom_in_pressed(self, box, event, _map):
        _map.zoom_in()

    def _on_zoom_out_pressed(self, box, event, _map):
        _map.zoom_out()

    def _on_zoom_slider_value_changed(self, adj, _map):
        zoom = adj.get_value()
        if zoom != _map.props.zoom:
            _map.set_zoom(int(zoom))

    def _on_map_zoom_changed(self, _map, paramspec):
        self.zoom.set_value(_map.props.zoom)
