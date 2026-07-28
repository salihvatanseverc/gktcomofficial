#include "MainWindow.h"

#include <QVBoxLayout>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QProcess>
#include <QWidget>

MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
{
    setWindowTitle("Göktürk Studio");
    resize(1000,700);

    QWidget *central = new QWidget(this);
    setCentralWidget(central);

    QVBoxLayout *layout = new QVBoxLayout(central);


    codeEditor = new QPlainTextEdit(this);
    codeEditor->setPlaceholderText(
        "Göktürk kodunuzu yazınız..."
    );


    runButton = new QPushButton(
        "Çalıştır",
        this
    );


    outputConsole = new QPlainTextEdit(this);
    outputConsole->setReadOnly(true);


    layout->addWidget(codeEditor);
    layout->addWidget(runButton);
    layout->addWidget(outputConsole);


    connect(
        runButton,
        &QPushButton::clicked,
        this,
        &MainWindow::runCode
    );
}



MainWindow::~MainWindow()
{

}



void MainWindow::runCode()
{
    outputConsole->clear();


    QString runtime =
    "C:/Users/Bruger/Desktop/Göktürk Programlama Dili/Language/Python/Windows Runtime/python.exe";


    QString kod =
    codeEditor->toPlainText();


    QProcess process;


    process.start(
        runtime,
        QStringList()
        << "-c"
        << kod
    );


    if(!process.waitForFinished())
    {
        outputConsole->appendPlainText(
            "Runtime başlatılamadı."
        );
        return;
    }


    QString cikti =
    process.readAllStandardOutput();


    QString hata =
    process.readAllStandardError();



    if(!cikti.isEmpty())
    {
        outputConsole->appendPlainText(cikti);
    }


    if(!hata.isEmpty())
    {
        outputConsole->appendPlainText(
            "Hata:\n" + hata
        );
    }
}