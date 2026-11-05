"""First-run orientation with a direct route into the drawing editor."""

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (
    QBoxLayout, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QToolButton, QVBoxLayout, QWidget, QSizePolicy, QLayout,
)


class StartScreen(QScrollArea):
    openRequested = Signal()
    returnRequested = Signal()

    def __init__(self, assets):
        super().__init__()
        self.setObjectName("startScreen")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("""
            QScrollArea#startScreen, QWidget#startContent { background: #1a2023; }
            QWidget#startContainer { background: transparent; }
            QLabel { background: transparent; color: #c2ced0; }
            QLabel#productName { color: #f1f4f2; font-size: 16px; font-weight: 600; }
            QLabel#heroTitle { color: #f1f4f2; font-size: 32px; font-weight: 600; }
            QLabel#intro { font-size: 15px; color: #bbc9cc; }
            QFrame#guide { background: #232d31; border: 1px solid #39494e; border-radius: 12px; }
            QFrame#guide QLabel { background: transparent; border: none; }
            QLabel#guideTitle { font-size: 19px; font-weight: 600; color: #edf3f1; }
            QLabel#stepTitle { font-size: 14px; font-weight: 600; color: #edf3f1; }
            QLabel#stepNumber { color: #a8ccbf; font-size: 14px; font-weight: 600; }
            QLabel#sectionTitle { color: #a8ccbf; font-weight: 600; }
            QToolButton#openProject { background: #a2c8bb; color: #172521; border: 1px solid #badacd;
                border-radius: 7px; padding: 13px 20px; font-size: 14px; font-weight: 600; }
            QToolButton#openProject:hover { background: #b8dbcf; }
            QToolButton#openProject:focus { border: 2px solid #f28583; }
            QToolButton#returnToDrawing { border: 1px solid #61736e; padding: 10px 20px; border-radius: 7px; }
        """)
        content = QWidget()
        content.setObjectName("startContent")
        self.setWidget(content)
        outside = QVBoxLayout(content)
        outside.setContentsMargins(32, 28, 32, 28)
        outside.addStretch(1)
        container = QWidget()
        self.container = container
        container.setObjectName("startContainer")
        outside.addWidget(container, 0, Qt.AlignmentFlag.AlignHCenter)
        outside.addStretch(1)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(30)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        brand = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(QIcon(str(assets / "start-brandmark.svg")).pixmap(54, 38))
        brand.addWidget(mark)
        brand.addWidget(self.label("OpenArch Swept Paths", "productName"))
        brand.addStretch()
        layout.addLayout(brand)

        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(48)
        layout.addLayout(self.columns)

        launch = QVBoxLayout()
        launch.setSpacing(14)
        launch.addWidget(self.label("Start a swept\npath study", "heroTitle"))
        launch.addWidget(self.label(
            "Open a site drawing, choose a design vehicle and test its route.", "intro"
        ))
        launch.addSpacing(8)
        self.open_button = QToolButton()
        self.open_button.setObjectName("openProject")
        self.open_button.setText("Open drawing or project")
        self.open_button.setIcon(QIcon(str(assets / "icons/open-start.svg")))
        self.open_button.setIconSize(QSize(26, 26))
        self.open_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.open_button.setMinimumHeight(86)
        self.open_button.clicked.connect(self.openRequested)
        launch.addWidget(self.open_button)
        launch.addWidget(self.label("DWG and DXF drawings · OASP editable projects"))
        self.return_button = QToolButton()
        self.return_button.setObjectName("returnToDrawing")
        self.return_button.setText("Return to drawing")
        self.return_button.setIcon(QIcon(str(assets / "icons/draw-path.svg")))
        self.return_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.return_button.clicked.connect(self.returnRequested)
        self.return_button.hide()
        launch.addWidget(self.return_button)
        launch.addSpacing(24)
        launch.addWidget(self.label("Move around", "sectionTitle"))
        launch.addWidget(self.label("Scroll to zoom. Hold the middle mouse button and drag to pan."))
        launch.addSpacing(6)
        undo = QKeySequence(QKeySequence.StandardKey.Undo).toString(QKeySequence.SequenceFormat.NativeText)
        redo = QKeySequence(QKeySequence.StandardKey.Redo).toString(QKeySequence.SequenceFormat.NativeText)
        launch.addWidget(self.label("Undo and redo", "sectionTitle"))
        launch.addWidget(self.label(
            f"{undo} undoes your last edit. {redo} restores it. Both work for waypoint edits and path changes."
        ))
        launch.addStretch(1)
        self.columns.addLayout(launch, 4)

        guide = QFrame()
        guide.setObjectName("guide")
        guide_layout = QVBoxLayout(guide)
        guide_layout.setContentsMargins(28, 26, 28, 26)
        guide_layout.setSpacing(22)
        guide_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        guide_layout.addWidget(self.label("From drawing to swept path", "guideTitle"))
        steps = (
            ("Open your drawing or project",
             "Start with a DWG or DXF site drawing. Open an .oasp project to continue a saved study."),
            ("Choose the vehicle",
             "Select a design vehicle in the right-hand panel. Check its dimensions, geometry and drawing scale."),
            ("Draw the route",
             "Click Draw path, then click on the drawing to place waypoints. The swept path previews as you go."),
            ("Finish, adjust or continue",
             "Press Esc, double-click or click Finish path. Drag waypoints to adjust the route. "
             "Click Draw path again to extend it, or New path to test another route."),
            ("Export and keep your work",
             "Paths DXF exports the swept paths. Drawing DXF includes the background. "
             "Save project creates an editable .oasp file so you can return to the study."),
        )
        for number, (title, detail) in enumerate(steps, 1):
            row = QHBoxLayout()
            row.setSpacing(16)
            index = self.label(str(number), "stepNumber")
            index.setFixedWidth(18)
            row.addWidget(index, 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(6)
            text.addWidget(self.label(title, "stepTitle"))
            text.addWidget(self.label(detail))
            row.addLayout(text, 1)
            guide_layout.addLayout(row)
        self.columns.addWidget(guide, 6)

    @staticmethod
    def label(text, name=""):
        label = QLabel(text)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        return label

    def resizeEvent(self, event):
        direction = QBoxLayout.Direction.TopToBottom if self.width() < 850 else QBoxLayout.Direction.LeftToRight
        self.columns.setDirection(direction)
        self.container.setFixedWidth(max(260, min(1120, self.viewport().width() - 64)))
        super().resizeEvent(event)
