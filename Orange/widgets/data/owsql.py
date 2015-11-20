import sys

import psycopg2
from PyQt4 import QtGui
from PyQt4.QtCore import Qt, QTimer
from PyQt4.QtGui import QApplication, QCursor, QMessageBox

from Orange.data import Table
from Orange.data.sql.table import SqlTable, LARGE_TABLE, AUTO_DL_LIMIT
from Orange.widgets import widget, gui
from Orange.widgets.settings import Setting
from Orange.widgets.widget import OutputSignal


MAX_DL_LIMIT = 1000000


class OWSql(widget.OWWidget):
    name = "SQL Table"
    id = "orange.widgets.data.sql"
    description = """
    Load dataset from SQL."""
    long_description = """
    Sql widget connects to server and opens data from there. """
    icon = "icons/SQLTable.svg"
    author = "Anze Staric"
    maintainer_email = "anze.staric@fri.uni-lj.si"
    priority = 10
    category = "Data"
    keywords = ["data", "file", "load", "read"]
    outputs = [OutputSignal(
        "Data", Table,
        doc="Attribute-valued data set read from the input file.")]

    want_main_area = False
    resizing_enabled = False

    host = Setting(None)
    port = Setting(None)
    database = Setting(None)
    username = Setting(None)
    password = Setting(None)
    table = Setting(None)
    sql = Setting("")
    guess_values = Setting(True)
    download = Setting(False)

    materialize = Setting(False)
    materialize_table_name = Setting("")

    def __init__(self):
        super().__init__()

        self._connection = None

        vbox = gui.widgetBox(self.controlArea, "Server", addSpace=True)
        box = gui.widgetBox(vbox)
        self.servertext = QtGui.QLineEdit(box)
        self.servertext.setPlaceholderText('Server')
        if self.host:
            self.servertext.setText(self.host if not self.port else
                                    '{}:{}'.format(self.host, self.port))
        box.layout().addWidget(self.servertext)
        self.databasetext = QtGui.QLineEdit(box)
        self.databasetext.setPlaceholderText('Database')
        if self.database:
            self.databasetext.setText(self.database)
        box.layout().addWidget(self.databasetext)
        self.usernametext = QtGui.QLineEdit(box)
        self.usernametext.setPlaceholderText('Username')
        if self.username:
            self.usernametext.setText(self.username)
        box.layout().addWidget(self.usernametext)
        self.passwordtext = QtGui.QLineEdit(box)
        self.passwordtext.setPlaceholderText('Password')
        self.passwordtext.setEchoMode(QtGui.QLineEdit.Password)
        if self.password:
            self.passwordtext.setText(self.password)
        box.layout().addWidget(self.passwordtext)

        tables = gui.widgetBox(box, orientation='horizontal')
        self.tablecombo = QtGui.QComboBox(
            tables,
            minimumContentsLength=35,
            sizeAdjustPolicy=QtGui.QComboBox.AdjustToMinimumContentsLength
        )
        tables.layout().addWidget(self.tablecombo)
        self.tablecombo.activated[int].connect(self.select_table)
        self.connectbutton = gui.button(
            tables, self, '↻', callback=self.connect)
        self.connectbutton.setSizePolicy(
            QtGui.QSizePolicy.Fixed, QtGui.QSizePolicy.Fixed)
        tables.layout().addWidget(self.connectbutton)

        self.custom_sql = gui.widgetBox(box, orientation='vertical')
        self.custom_sql.setVisible(False)
        self.sqltext = QtGui.QTextEdit(self.custom_sql)
        self.sqltext.setPlainText(self.sql)
        self.custom_sql.layout().addWidget(self.sqltext)

        mt = gui.widgetBox(self.custom_sql, orientation='horizontal')
        gui.checkBox(mt, self, 'materialize', 'materialize to table ')
        gui.lineEdit(mt, self, 'materialize_table_name')

        self.executebtn = gui.button(
            self.custom_sql, self, 'Execute', callback=self.open_table)

        box.layout().addWidget(self.custom_sql)

        gui.checkBox(box, self, "guess_values",
                     "Auto-discover discrete variables",
                     callback=self.open_table)

        gui.checkBox(box, self, "download",
                     "Download data to local memory",
                     callback=self.open_table)

        self.connect()
        if self.table:
            QTimer.singleShot(0, self.open_table)

    def error(self, id=0, text=""):
        super().error(id, text)
        if 'server' in text or 'host' in text:
            self.servertext.setStyleSheet('QLineEdit {border: 2px solid red;}')
        else:
            self.servertext.setStyleSheet('')
        if 'role' in text:
            self.usernametext.setStyleSheet('QLineEdit {border: 2px solid red;}')
        else:
            self.usernametext.setStyleSheet('')
        if 'database' in text:
            self.databasetext.setStyleSheet('QLineEdit {border: 2px solid red;}')
        else:
            self.databasetext.setStyleSheet('')


    def connect(self):
        hostport = self.servertext.text().split(':')
        self.host = hostport[0]
        self.port = hostport[1] if len(hostport) == 2 else None
        self.database = self.databasetext.text()
        self.username = self.usernametext.text() or None
        self.password = self.passwordtext.text() or None
        try:
            self._connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.username,
                password=self.password
            )
            self.error(0)
            self.refresh_tables()
        except psycopg2.Error as err:
            self.error(0, str(err).split('\n')[0])
            self.tablecombo.clear()


    def refresh_tables(self):
        self.tablecombo.clear()
        if self._connection is None:
            return

        cur = self._connection.cursor()
        cur.execute("""SELECT --n.nspname as "Schema",
                              c.relname AS "Name"
                       FROM pg_catalog.pg_class c
                  LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                      WHERE c.relkind IN ('r','v','m','S','f','')
                        AND n.nspname <> 'pg_catalog'
                        AND n.nspname <> 'information_schema'
                        AND n.nspname !~ '^pg_toast'
                        AND pg_catalog.pg_table_is_visible(c.oid)
                        AND NOT c.relname LIKE '\\_\\_%'
                   ORDER BY 1;""")

        self.tablecombo.addItem("Select a table")
        for i, (table_name,) in enumerate(cur.fetchall()):
            self.tablecombo.addItem(table_name)
            if table_name == self.table:
                self.tablecombo.setCurrentIndex(i + 1)
        self.tablecombo.addItem("Custom SQL")

    def select_table(self):
        curIdx = self.tablecombo.currentIndex()
        if self.tablecombo.itemText(curIdx) != "Custom SQL":
            self.custom_sql.setVisible(False)
            return self.open_table()
        else:
            self.custom_sql.setVisible(True)
            self.table = None

    def open_table(self):
        if self.tablecombo.currentIndex() <= 0:
            return

        if self.tablecombo.currentIndex() < self.tablecombo.count() - 1:
            self.table = self.tablecombo.currentText()
        else:
            self.sql = self.table = self.sqltext.toPlainText()
            if self.materialize:
                try:
                    cur = self._connection.cursor()
                    cur.execute("DROP TABLE IF EXISTS " + self.materialize_table_name)
                    cur.execute("CREATE TABLE " + self.materialize_table_name + " AS " + self.table)
                    cur.execute("ANALYZE " + self.materialize_table_name)
                    self.table = self.materialize_table_name
                except psycopg2.ProgrammingError as ex:
                    self.error(0, str(ex))
                    return
                finally:
                    self._connection.commit()

        try:
            table = SqlTable(dict(host=self.host,
                                  port=self.port,
                                  database=self.database,
                                  user=self.username,
                                  password=self.password),
                             self.table,
                             inspect_values=False)
        except psycopg2.ProgrammingError as ex:
            self.error(0, str(ex))
            return

        self.error(0)


        sample = False
        if table.approx_len() > LARGE_TABLE and self.guess_values:
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Warning)
            confirm.setText("Attribute discovery might take "
                            "a long time on large tables.\n"
                            "Do you want to auto discover attributes?")
            confirm.addButton("Yes", QMessageBox.YesRole)
            no_button = confirm.addButton("No", QMessageBox.NoRole)
            sample_button = confirm.addButton("Yes, on a sample",
                                              QMessageBox.YesRole)
            confirm.exec()
            if confirm.clickedButton() == no_button:
                self.guess_values = False
            elif confirm.clickedButton() == sample_button:
                sample = True

        self.information(1)
        if self.guess_values:
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            if sample:
                s = table.sample_time(1)
                domain = s.get_domain(guess_values=True)
                self.information(
                    1, "Domain was generated from a sample of the table.")
            else:
                domain = table.get_domain(guess_values=True)
            QApplication.restoreOverrideCursor()
            table.domain = domain

        if self.download:
            if table.approx_len() > MAX_DL_LIMIT:
                QMessageBox.warning(
                    self, 'Warning', "Data is too big to download.\n"
                    "Consider using the Data Sampler widget to download "
                    "a sample instead.")
                self.download = False
            elif table.approx_len() > AUTO_DL_LIMIT:
                confirm = QMessageBox.question(
                    self, 'Question', "Data appears to be big. Do you really "
                                      "want to download it to local memory?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if confirm == QMessageBox.No:
                    self.download = False
        if self.download:
            table.download_data(MAX_DL_LIMIT)
            table = Table(table)

        self.send("Data", table)

if __name__ == "__main__":
    import os

    a = QtGui.QApplication(sys.argv)
    ow = OWSql()
    ow.show()
    a.exec_()
    ow.saveSettings()
