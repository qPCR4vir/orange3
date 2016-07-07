"""
Image Viewer Widget
-------------------

"""
import sys
import os
import weakref
import logging
from xml.sax.saxutils import escape
from collections import namedtuple
from functools import partial
from itertools import zip_longest

import numpy

from PyQt4.QtGui import (
    QGraphicsScene, QGraphicsView, QGraphicsWidget, QGraphicsItem,
    QGraphicsTextItem, QGraphicsRectItem, QGraphicsLinearLayout,
    QGraphicsGridLayout, QSizePolicy, QPixmap, QPen, QBrush, QColor,
    QPainter, QPainterPath, QApplication, QImageReader
)
from PyQt4.QtCore import (
    Qt, QObject, QEvent, QThread, QSizeF, QRectF, QPointF, QUrl, QDir
)

from PyQt4.QtCore import pyqtSignal as Signal
from PyQt4.QtNetwork import (
    QNetworkAccessManager, QNetworkDiskCache, QNetworkRequest, QNetworkReply
)

import Orange.data
from Orange.widgets import widget, gui, settings
from Orange.widgets.utils.itemmodels import VariableListModel

from concurrent.futures import Future

_log = logging.getLogger(__name__)


class GraphicsPixmapWidget(QGraphicsWidget):
    def __init__(self, pixmap, parent=None):
        QGraphicsWidget.__init__(self, parent)
        self.setCacheMode(QGraphicsItem.ItemCoordinateCache)
        self._pixmap = pixmap
        self._pixmapSize = QSizeF()
        self._keepAspect = True
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def setPixmap(self, pixmap):
        if self._pixmap != pixmap:
            self._pixmap = QPixmap(pixmap)
            self.updateGeometry()
            self.update()

    def pixmap(self):
        return QPixmap(self._pixmap)

    def sizeHint(self, which, constraint=QSizeF()):
        if which == Qt.PreferredSize:
            return QSizeF(self._pixmap.size())
        else:
            return QGraphicsWidget.sizeHint(self, which, constraint)

    def setKeepAspectRatio(self, keep):
        if self._keepAspect != keep:
            self._keepAspect = bool(keep)
            self.update()

    def keepAspectRatio(self):
        return self._keepAspect

    def paint(self, painter, option, widget=0):
        if self._pixmap.isNull():
            return

        rect = self.contentsRect()
        pixsize = QSizeF(self._pixmap.size())
        aspectmode = (Qt.KeepAspectRatio if self._keepAspect
                      else Qt.IgnoreAspectRatio)
        pixsize.scale(rect.size(), aspectmode)
        pixrect = QRectF(QPointF(0, 0), pixsize)
        pixrect.moveCenter(rect.center())

        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 50), 3))
        painter.drawRoundedRect(pixrect, 2, 2)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        source = QRectF(QPointF(0, 0), QSizeF(self._pixmap.size()))
        painter.drawPixmap(pixrect, self._pixmap, source)
        painter.restore()


class GraphicsTextWidget(QGraphicsWidget):
    def __init__(self, text, parent=None):
        QGraphicsWidget.__init__(self, parent)
        self.labelItem = QGraphicsTextItem(self)
        self.setHtml(text)

        self.labelItem.document().documentLayout().documentSizeChanged.connect(
            self.onLayoutChanged
        )

    def onLayoutChanged(self, *args):
        self.updateGeometry()

    def sizeHint(self, which, constraint=QSizeF()):
        if which == Qt.MinimumSize:
            return self.labelItem.boundingRect().size()
        else:
            return self.labelItem.boundingRect().size()

    def setTextWidth(self, width):
        self.labelItem.setTextWidth(width)

    def setHtml(self, text):
        self.labelItem.setHtml(text)


class GraphicsThumbnailWidget(QGraphicsWidget):
    def __init__(self, pixmap, title="", parent=None):
        QGraphicsWidget.__init__(self, parent)

        self._title = None
        self._size = QSizeF()

        layout = QGraphicsLinearLayout(Qt.Vertical, self)
        layout.setSpacing(2)
        layout.setContentsMargins(5, 5, 5, 5)
        self.setContentsMargins(0, 0, 0, 0)

        self.pixmapWidget = GraphicsPixmapWidget(pixmap, self)
        self.labelWidget = GraphicsTextWidget(title, self)

        layout.addItem(self.pixmapWidget)
        layout.addItem(self.labelWidget)
        layout.addStretch()
        layout.setAlignment(self.pixmapWidget, Qt.AlignCenter)
        layout.setAlignment(self.labelWidget, Qt.AlignHCenter | Qt.AlignBottom)

        self.setLayout(layout)

        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setTitle(title)
        self.setTitleWidth(100)

    def setPixmap(self, pixmap):
        self.pixmapWidget.setPixmap(pixmap)
        self._updatePixmapSize()

    def pixmap(self):
        return self.pixmapWidget.pixmap()

    def setTitle(self, title):
        if self._title != title:
            self._title = title
            self.labelWidget.setHtml(
                '<center>' + escape(title) + '</center>'
            )
            self.layout().invalidate()

    def title(self):
        return self._title

    def setThumbnailSize(self, size):
        if self._size != size:
            self._size = QSizeF(size)
            self._updatePixmapSize()
            self.labelWidget.setTextWidth(max(100, size.width()))

    def setTitleWidth(self, width):
        self.labelWidget.setTextWidth(width)
        self.layout().invalidate()

    def paint(self, painter, option, widget=0):
        contents = self.contentsRect()
        if self.isSelected():
            painter.save()
            painter.setPen(QPen(QColor(125, 162, 206, 192)))
            painter.setBrush(QBrush(QColor(217, 232, 252, 192)))
            painter.drawRoundedRect(
                QRectF(contents.topLeft(), self.geometry().size()), 3, 3)
            painter.restore()

    def _updatePixmapSize(self):
        pixsize = QSizeF(self._size)
        self.pixmapWidget.setMinimumSize(pixsize)
        self.pixmapWidget.setMaximumSize(pixsize)


class ThumbnailWidget(QGraphicsWidget):
    def __init__(self, parent=None):
        QGraphicsWidget.__init__(self, parent)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        self.setContentsMargins(10, 10, 10, 10)
        layout = QGraphicsGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.setLayout(layout)

    def setGeometry(self, geom):
        super(ThumbnailWidget, self).setGeometry(geom)
        self.reflow(self.size().width())

    def event(self, event):
        if event.type() == QEvent.LayoutRequest:
            sh = self.effectiveSizeHint(Qt.PreferredSize)
            self.resize(sh)
            self.layout().activate()
            event.accept()
            return True
        else:
            return super().event(event)

    def reflow(self, width):
        if not self.layout():
            return

        left, right, _, _ = self.getContentsMargins()
        layout = self.layout()
        width -= left + right

        hints = self._hints(Qt.PreferredSize)
        widths = [max(24, h.width()) for h in hints]
        ncol = self._fitncols(widths, layout.horizontalSpacing(), width)

        if ncol == layout.columnCount():
            return

        items = [layout.itemAt(i) for i in range(layout.count())]

        # remove all items from the layout, then re-add them back in
        # updated positions
        for item in items:
            layout.removeItem(item)

        for i, item in enumerate(items):
            layout.addItem(item, i // ncol, i % ncol)

        layout.invalidate()

    def items(self):
        layout = self.layout()
        if layout:
            return [layout.itemAt(i) for i in range(layout.count())]
        else:
            return []

    def _hints(self, which):
        return [item.effectiveSizeHint(which) for item in self.items()]

    def _fitncols(self, widths, spacing, constraint):
        def sliced(seq, ncol):
            return [seq[i:i + ncol] for i in range(0, len(seq), ncol)]

        def flow_width(widths, spacing, ncol):
            W = sliced(widths, ncol)
            col_widths = map(max, zip_longest(*W, fillvalue=0))
            return sum(col_widths) + (ncol - 1) * spacing

        ncol_best = 1
        for ncol in range(2, len(widths) + 1):
            w = flow_width(widths, spacing, ncol)
            if w <= constraint:
                ncol_best = ncol
            else:
                break

        return ncol_best


class GraphicsScene(QGraphicsScene):
    selectionRectPointChanged = Signal(QPointF)

    def __init__(self, *args):
        QGraphicsScene.__init__(self, *args)
        self.selectionRect = None

    def mousePressEvent(self, event):
        QGraphicsScene.mousePressEvent(self, event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            screenPos = event.screenPos()
            buttonDown = event.buttonDownScreenPos(Qt.LeftButton)
            if (screenPos - buttonDown).manhattanLength() > 2.0:
                self.updateSelectionRect(event)
        QGraphicsScene.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.selectionRect:
                self.removeItem(self.selectionRect)
                self.selectionRect = None
        QGraphicsScene.mouseReleaseEvent(self, event)

    def updateSelectionRect(self, event):
        pos = event.scenePos()
        buttonDownPos = event.buttonDownScenePos(Qt.LeftButton)
        rect = QRectF(pos, buttonDownPos).normalized()
        rect = rect.intersected(self.sceneRect())
        if not self.selectionRect:
            self.selectionRect = QGraphicsRectItem()
            self.selectionRect.setBrush(QColor(10, 10, 10, 20))
            self.selectionRect.setPen(QPen(QColor(200, 200, 200, 200)))
            self.addItem(self.selectionRect)
        self.selectionRect.setRect(rect)
        if event.modifiers() & Qt.ControlModifier or \
                        event.modifiers() & Qt.ShiftModifier:
            path = self.selectionArea()
        else:
            path = QPainterPath()
        path.addRect(rect)
        self.setSelectionArea(path)
        self.selectionRectPointChanged.emit(pos)


_ImageItem = namedtuple(
    "_ImageItem",
    ["index",   # Row index in the input data table
     "widget",  # GraphicsThumbnailWidget displaying the image.
     "url",     # Composed final image url.
     "future"]  # Future instance yielding an QImage
)


class OWImageViewer(widget.OWWidget):
    name = "Image Viewer"
    description = "View images referred to in the data."
    icon = "icons/ImageViewer.svg"
    priority = 4050

    inputs = [("Data", Orange.data.Table, "setData")]
    outputs = [("Data", Orange.data.Table, )]

    settingsHandler = settings.DomainContextHandler()

    imageAttr = settings.ContextSetting(0)
    titleAttr = settings.ContextSetting(0)

    imageSize = settings.Setting(100)
    autoCommit = settings.Setting(False)

    buttons_area_orientation = Qt.Vertical
    graph_name = "scene"

    def __init__(self):
        super().__init__()
        self.data = None
        self.allAttrs = []
        self.stringAttrs = []

        self.thumbnailWidget = None
        self.sceneLayout = None
        self.selectedIndices = []

        #: List of _ImageItems
        self.items = []

        self._errcount = 0
        self._successcount = 0

        self.info = gui.widgetLabel(
            gui.vBox(self.controlArea, "Info"),
            "Waiting for input.\n"
        )

        self.imageAttrCB = gui.comboBox(
            self.controlArea, self, "imageAttr",
            box="Image Filename Attribute",
            tooltip="Attribute with image filenames",
            callback=[self.clearScene, self.setupScene],
            contentsLength=12,
            addSpace=True,
        )

        self.titleAttrCB = gui.comboBox(
            self.controlArea, self, "titleAttr",
            box="Title Attribute",
            tooltip="Attribute with image title",
            callback=self.updateTitles,
            contentsLength=12,
            addSpace=True
        )

        gui.hSlider(
            self.controlArea, self, "imageSize",
            box="Image Size", minValue=32, maxValue=1024, step=16,
            callback=self.updateSize,
            createLabel=False
        )
        gui.rubber(self.controlArea)

        gui.auto_commit(self.buttonsArea, self, "autoCommit", "Send", box=False)

        self.scene = GraphicsScene()
        self.sceneView = QGraphicsView(self.scene, self)
        self.sceneView.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.sceneView.setRenderHint(QPainter.Antialiasing, True)
        self.sceneView.setRenderHint(QPainter.TextAntialiasing, True)
        self.sceneView.setFocusPolicy(Qt.WheelFocus)
        self.sceneView.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.sceneView.installEventFilter(self)
        self.mainArea.layout().addWidget(self.sceneView)

        self.scene.selectionChanged.connect(self.onSelectionChanged)
        self.scene.selectionRectPointChanged.connect(
            self.onSelectionRectPointChanged, Qt.QueuedConnection
        )
        self.resize(800, 600)

        self.loader = ImageLoader(self)

    def setData(self, data):
        self.closeContext()
        self.clear()

        self.data = data

        if data is not None:
            domain = data.domain
            self.allAttrs = domain.variables + domain.metas
            self.stringAttrs = [a for a in self.allAttrs if a.is_string]

            self.stringAttrs = sorted(
                self.stringAttrs,
                key=lambda attr: 0 if "type" in attr.attributes else 1
            )

            indices = [i for i, var in enumerate(self.stringAttrs)
                       if var.attributes.get("type") == "image"]
            if indices:
                self.imageAttr = indices[0]

            self.imageAttrCB.setModel(VariableListModel(self.stringAttrs))
            self.titleAttrCB.setModel(VariableListModel(self.allAttrs))

            self.openContext(data)

            self.imageAttr = max(min(self.imageAttr, len(self.stringAttrs) - 1), 0)
            self.titleAttr = max(min(self.titleAttr, len(self.allAttrs) - 1), 0)

            if self.stringAttrs:
                self.setupScene()
        else:
            self.info.setText("Waiting for input.\n")

    def clear(self):
        self.data = None
        self.information(0)
        self.error(0)
        self.imageAttrCB.clear()
        self.titleAttrCB.clear()
        self.clearScene()

    def setupScene(self):
        self.information(0)
        self.error(0)
        if self.data:
            attr = self.stringAttrs[self.imageAttr]
            titleAttr = self.allAttrs[self.titleAttr]
            instances = [inst for inst in self.data
                         if numpy.isfinite(inst[attr])]
            widget = ThumbnailWidget()
            layout = widget.layout()
            size = QSizeF(self.imageSize, self.imageSize)
            self.scene.addItem(widget)

            for i, inst in enumerate(instances):
                url = self.urlFromValue(inst[attr])
                title = str(inst[titleAttr])

                thumbnail = GraphicsThumbnailWidget(
                    QPixmap(), title=title, parent=widget
                )
                thumbnail.setThumbnailSize(size)
                thumbnail.setToolTip(url.toString())
                thumbnail.instance = inst
                layout.addItem(thumbnail, i / 5, i % 5)

                if url.isValid():
                    future = self.loader.get(url)

                    @future.add_done_callback
                    def set_pixmap(future, thumb=thumbnail):
                        if future.cancelled():
                            return

                        assert future.done()

                        if future.exception():
                            # Should be some generic error image.
                            pixmap = QPixmap()
                            thumb.setToolTip(thumb.toolTip() + "\n" +
                                             str(future.exception()))
                        else:
                            pixmap = QPixmap.fromImage(future.result())

                        thumb.setPixmap(pixmap)

                        self._updateStatus(future)

                else:
                    future = None
                self.items.append(_ImageItem(i, thumbnail, url, future))

            widget.show()
            widget.geometryChanged.connect(self._updateSceneRect)

            self.info.setText("Retrieving...\n")
            self.thumbnailWidget = widget
            self.sceneLayout = layout

        if self.sceneLayout:
            self._updateGeometryConstraints()

    def urlFromValue(self, value):
        variable = value.variable
        origin = variable.attributes.get("origin", "")
        if origin and QDir(origin).exists():
            origin = QUrl.fromLocalFile(origin)
        elif origin:
            origin = QUrl(origin)
            if not origin.scheme():
                origin.setScheme("file")
        else:
            origin = QUrl("")
        base = origin.path()
        if base.strip() and not base.endswith("/"):
            origin.setPath(base + "/")

        name = QUrl(str(value))
        url = origin.resolved(name)
        if not url.scheme():
            url.setScheme("file")
        return url

    def _cancelAllFutures(self):
        for item in self.items:
            if item.future is not None:
                item.future.cancel()
                if item.future._reply is not None:
                    item.future._reply.close()
                    item.future._reply.deleteLater()
                    item.future._reply = None

    def clearScene(self):
        self._cancelAllFutures()

        self.items = []
        self.thumbnailWidget = None
        self.sceneLayout = None
        self.scene.clear()
        self._errcount = 0
        self._successcount = 0

    def thumbnailItems(self):
        return [item.widget for item in self.items]

    def updateSize(self):
        size = QSizeF(self.imageSize, self.imageSize)
        for item in self.thumbnailItems():
            item.setThumbnailSize(size)

        self._updateGeometryConstraints()

    def updateTitles(self):
        titleAttr = self.allAttrs[self.titleAttr]
        for item in self.items:
            item.widget.setTitle(str(item.widget.instance[titleAttr]))

    def onSelectionChanged(self):
        selected = [item for item in self.items if item.widget.isSelected()]
        self.selectedIndices = [item.index for item in selected]
        self.commit()

    def onSelectionRectPointChanged(self, point):
        self.sceneView.ensureVisible(QRectF(point, QSizeF(1, 1)), 5, 5)

    def commit(self):
        if self.data:
            if self.selectedIndices:
                selected = self.data[self.selectedIndices]
            else:
                selected = None
            self.send("Data", selected)
        else:
            self.send("Data", None)

    def _updateStatus(self, future):
        if future.cancelled():
            return

        if future.exception():
            self._errcount += 1
            _log.debug("Error: %r", future.exception())
        else:
            self._successcount += 1

        count = len([item for item in self.items if item.future is not None])
        self.info.setText(
            "Retrieving:\n" +
            "{} of {} images".format(self._successcount, count))

        if self._errcount + self._successcount == count:
            if self._errcount:
                self.info.setText(
                    "Done:\n" +
                    "{} images, {} errors".format(count, self._errcount)
                )
            else:
                self.info.setText(
                    "Done:\n" +
                    "{} images".format(count)
                )
            attr = self.stringAttrs[self.imageAttr]
            if self._errcount == count and not "type" in attr.attributes:
                self.error(0,
                           "No images found! Make sure the '%s' attribute "
                           "is tagged with 'type=image'" % attr.name)

    def _updateSceneRect(self):
        self.scene.setSceneRect(self.scene.itemsBoundingRect())

    def _updateGeometryConstraints(self):
        # Update the thumbnail grid widget's width constraint (derived from
        # the viewport's width
        if self.thumbnailWidget is not None:
            width = (self.sceneView.width() -
                     self.sceneView.verticalScrollBar().width())
            width = width - 2
            self.thumbnailWidget.reflow(width)
            self.thumbnailWidget.setPreferredWidth(width)
            self.thumbnailWidget.layout().activate()

    def onDeleteWidget(self):
        self._cancelAllFutures()
        self.clear()

    def eventFilter(self, receiver, event):
        if receiver is self.sceneView and event.type() == QEvent.Resize \
                and self.thumbnailWidget:
            self._updateGeometryConstraints()

        return super(OWImageViewer, self).eventFilter(receiver, event)


class ImageLoader(QObject):
    #: A weakref to a QNetworkAccessManager used for image retrieval.
    #: (we can only have one QNetworkDiskCache opened on the same
    #: directory)
    _NETMANAGER_REF = None

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        assert QThread.currentThread() is QApplication.instance().thread()

        netmanager = self._NETMANAGER_REF and self._NETMANAGER_REF()
        if netmanager is None:
            netmanager = QNetworkAccessManager()
            cache = QNetworkDiskCache()
            cache.setCacheDirectory(
                os.path.join(settings.widget_settings_dir(),
                             __name__ + ".ImageLoader.Cache")
            )
            netmanager.setCache(cache)
            ImageLoader._NETMANAGER_REF = weakref.ref(netmanager)
        self._netmanager = netmanager

    def get(self, url):
        future = Future()
        url = QUrl(url)
        request = QNetworkRequest(url)
        request.setRawHeader(b"User-Agent", b"OWImageViewer/1.0")
        request.setAttribute(
            QNetworkRequest.CacheLoadControlAttribute,
            QNetworkRequest.PreferCache
        )

        # Future yielding a QNetworkReply when finished.
        reply = self._netmanager.get(request)
        future._reply = reply

        @future.add_done_callback
        def abort_on_cancel(f):
            # abort the network request on future.cancel()
            if f.cancelled() and f._reply is not None:
                f._reply.abort()

        n_redir = 0

        def on_reply_ready(reply, future):
            nonlocal n_redir
            # schedule deferred delete to ensure the reply is closed
            # otherwise we will leak file/socket descriptors
            reply.deleteLater()
            future._reply = None
            if reply.error() == QNetworkReply.OperationCanceledError:
                # The network request was cancelled
                reply.close()
                future.cancel()
                return

            if reply.error() != QNetworkReply.NoError:
                # XXX Maybe convert the error into standard
                # http and urllib exceptions.
                future.set_exception(Exception(reply.errorString()))
                reply.close()
                return

            # Handle a possible redirection
            location = reply.attribute(
                QNetworkRequest.RedirectionTargetAttribute)

            if location is not None and n_redir < 1:
                n_redir += 1
                location = reply.url().resolved(location)
                # Retry the original request with a new url.
                request = QNetworkRequest(reply.request())
                request.setUrl(location)
                newreply = self._netmanager.get(request)
                future._reply = newreply
                newreply.finished.connect(
                    partial(on_reply_ready, newreply, future))
                reply.close()
                return

            reader = QImageReader(reply)
            image = reader.read()
            reply.close()

            if image.isNull():
                future.set_exception(Exception(reader.errorString()))
            else:
                future.set_result(image)

        reply.finished.connect(partial(on_reply_ready, reply, future))
        return future


def main(argv=sys.argv):
    import sip

    app = QApplication(argv)
    argv = app.arguments()
    w = OWImageViewer()
    w.show()
    w.raise_()

    if len(argv) > 1:
        data = Orange.data.Table(argv[1])
    else:
        data = Orange.data.Table('zoo-with-images')

    w.setData(data)
    rval = app.exec_()
    w.saveSettings()
    w.onDeleteWidget()
    sip.delete(w)
    app.processEvents()
    return rval


if __name__ == "__main__":
    main()
