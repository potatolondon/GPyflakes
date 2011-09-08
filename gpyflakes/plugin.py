#!/usr/bin/env python

#    GPyflakes - (Yet another) Pyflakes Integration Plugin for GEdit
#    Copyright (C) 2011  Luke Benstead

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
    
from gi.repository import GObject, Gedit, Gtk
from threading import Thread
from subprocess import Popen
import tempfile
import os
import re

class PyflakesErrorModel(Gtk.ListStore):
    def __init__ (self):
        super(PyflakesErrorModel, self).__init__ (int, str)

    def add(self, line, message):
        self.append([int(line), message])

class PyflakesErrorPane(Gtk.TreeView):
    def __init__(self, window):
        Gtk.TreeView.__init__(self)
        
        self._window = window

        linha = Gtk.TreeViewColumn ("Line")
        linha_cell = Gtk.CellRendererText ()
        linha.pack_start (linha_cell, False)
        linha.add_attribute (linha_cell, 'text', 0)
        linha.set_sort_column_id (0)
        self.append_column (linha)

        msg = Gtk.TreeViewColumn ("Message")
        msg_cell = Gtk.CellRendererText ()
        msg.pack_start (msg_cell, False)
        msg.add_attribute (msg_cell, 'text', 1)
        msg.set_sort_column_id (1)        
        self.append_column (msg)

        self.set_model(PyflakesErrorModel())
        self.connect ("row-activated", self.on_row_activated)
        
    def clear_results(self):
        self.get_model().clear()
        
    def add_result(self, result):
        self.get_model().add(result["line"], result["error"])

    def on_row_activated(self, view, row, column):
        model = view.get_model ()
        it = model.get_iter (row)
        
        window = self._window
        doc = window.get_active_document()
        line = model.get_value(it, 0) - 1
        if doc:
            doc.goto_line (line)
            view = window.get_active_view()
            text_iter = doc.get_iter_at_line(line)
            view.scroll_to_iter(text_iter, 0.25, False, 0.5, 0.5)
        
class PyflakesRun(Thread):
    def __init__(self, python_file):
        Thread.__init__(self)
        
        self._python_file = python_file
        self._finished = False
        self._failed = False
        
    def is_complete(self):
        return self._finished
    
    def is_failed(self):
        return self._failed
    
    def get_results(self):
        return self._results
        
    def _run_and_grab_output(self):
        out_file = tempfile.NamedTemporaryFile("w+", suffix=".pyflakes-output")
        cwd = os.path.dirname(self._python_file)
        
        #Follow up the tree of directories looking for python packages until we find the root 
        #of the project
        while os.path.exists(os.path.join(cwd, '__init__.py')):
            cwd = os.path.dirname(cwd)
            if not cwd:
                cwd = os.path.dirname(self._python_file)
                break
            
        print("Spawning pyflakes process")
        process = Popen(["pyflakes", self._python_file],
                        stdout=out_file,
                        stderr=open('/dev/null', "w"),
                        cwd=cwd)
                        
        print("Waiting...")
        process.wait()
        
        out_file.flush()
        out_file.seek(0)
        
        print("\nRun complete, processing results")
        results = out_file.read()
        out_file.close()
        
        self._results = self._parse_results(results)
        
    def _parse_results(self, results):
        errors_to_display = []
        for line in results.split('\n'):
            regex = "^(?P<filename>.+?):(?P<line>\d+):\s(?P<error>(.+?))$"
            match = re.match(regex, line)
            if match:
                errors_to_display.append({
                    'filename' : match.group('filename'),
                    'line' : match.group('line'),
                    'error' : match.group('error')
                })
                
        return errors_to_display
            
    def run(self):
        try:
            print("Running pyflakes on file: %s" % self._python_file)
            self._run_and_grab_output()
        except Exception, e:
            print("Error during pyflakes run")
            print(str(e))
            self._failed = True

        self._finished = True
    

class PyflakesPlugin(GObject.Object, Gedit.WindowActivatable):
    __gtype_name__ = "PyflakesPlugin"
    window = GObject.property(type=Gedit.Window)
    
    def __init__(self):
        GObject.Object.__init__(self)
        
        self._document_save_handlers = {}
        self._tree_view = None
        self._pyflakes_threads = []
        self._document_results = {}
    
        self._threads_running = False
    
    def _attach_document_saved_signals(self):
        for document in self.window.get_documents():
            print "Connecting to document: ", document.get_location()
            self._add_document_save_handler(document)
    
    def _detach_document_saved_signals(self):
        for k in self._document_save_handlers:
            k.disconnect(self._document_save_handlers[k])
            
        self._document_save_handlers = {}

    def do_activate(self):
        print "Window %s activated." % self.window
        self._create_bottom_pane()

        self._attach_document_saved_signals()
        
        self._tab_added_sig_id = self.window.connect("tab-added", self.on_tab_added)
        self._tab_removed_sig_id = self.window.connect("tab-removed", self.on_tab_removed)        

        self._threads_running = False
        
        self.window.get_active_view().set_show_line_marks(True)

    def do_deactivate(self):
        print "Window %s deactivated." % self.window
        self._destroy_bottom_pane()
        
        self.window.disconnect(self._tab_added_sig_id)
        self.window.disconnect(self._tab_removed_sig_id)
        
        self._detach_document_saved_signals()

    def do_update_state(self):
        self.redisplay_results()
        
    def _create_bottom_pane(self):
        icon = Gtk.Image.new_from_stock(Gtk.STOCK_YES, Gtk.IconSize.MENU)
        parent = self.window.get_bottom_panel()
        self._tree_view = PyflakesErrorPane(self.window)
        
        self._scrolled_window = Gtk.ScrolledWindow()
        #self._scrolled_window.set_policy(Gtk.POLICY_NEVER, Gtk.POLICY_AUTOMATIC)
        self._scrolled_window.add(self._tree_view)
        self._scrolled_window.show_all()
        
        parent.add_item(self._scrolled_window, "PyflakesResults", "Pyflakes Results", icon)
        parent.activate_item(self._scrolled_window)
        
    def _destroy_bottom_pane(self):
        parent = self.window.get_bottom_panel()
        parent.remove_item(self._scrolled_window)

    def redisplay_results(self):
        self._tree_view.clear_results()
        
        document = self.window.get_active_document()
        if document is None:
            return
            
        document.remove_source_marks(document.get_start_iter(), document.get_end_iter(), "ERROR")
        document.remove_source_marks(document.get_start_iter(), document.get_end_iter(), "WARNING")
        document.remove_source_marks(document.get_start_iter(), document.get_end_iter(), "INFO")                
        
        if document in self._document_results:
            i = 0
            for result in self._document_results[document]:
                self._tree_view.add_result(result)
                
                it = document.get_iter_at_line(int(result["line"]))
                assert it
#                print("Adding marker")
                #document.create_source_mark(str(i), marker_type, it)
                i += 1
        else:
            print("Couldn't find document in the Pyflakes result set?")

    def process_results_task(self):
        to_remove = []
        
        for thread in self._pyflakes_threads:
            if thread.is_complete() and not thread.is_failed():
                print("Adding pylint results to document")
                
                results = thread.get_results()
                self._document_results[self.window.get_active_document()] = results
                to_remove.append(thread)
                
                self.redisplay_results()
            elif thread.is_complete():
                to_remove.append(thread)
            else:                
                pass #Still running
                
        for thread in to_remove:
            print("Removing thread")
            self._pyflakes_threads.remove(thread)
        
        to_remove = []
    
        self._threads_running = bool(self._pyflakes_threads)
        if not self._threads_running:
            print("Removing idle handler")
            
        return self._threads_running

    def _run_pylint_on_document(self, document):
        new_thread = PyflakesRun(document.get_uri_for_display())
        self._pyflakes_threads.append(new_thread)
        new_thread.start()    
        
        #Add a task to process the results from the pylint threads
        if not self._threads_running:
            print("Adding idle handler")
            GObject.idle_add(self.process_results_task)
            self._threads_running = True

    def on_document_saved(self, document, error):
        print "Document has been saved: ", document.get_short_name_for_display() 
        self._run_pylint_on_document(document)
        
    def _add_document_save_handler(self, document):
        print "Connecting document saved signal to %s" % document.get_short_name_for_display()
        assert document not in self._document_save_handlers

        self._document_save_handlers[document] = document.connect("saved", self.on_document_saved)
        
    def _remove_document_save_handler(self, document):
        print "Disconnecting document saved signal to %s" % document.get_short_name_for_display()    
        assert document in self._document_save_handlers
        
        document.disconnect(self._document_save_handlers[document])
        del self._document_save_handlers[document]
    
    def on_tab_added(self, window, tab, data=None):
        document = tab.get_document()
        self._add_document_save_handler(document)
        self._run_pylint_on_document(document)
    
    def on_tab_removed(self, window, tab, data=None):
        document = tab.get_document()
        self._remove_document_save_handler(document)

