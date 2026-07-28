#pragma once

#include <QMainWindow>

class QPlainTextEdit;
class QPushButton;

class MainWindow : public QMainWindow
{
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow();

private slots:
    void runCode();

private:
    QPlainTextEdit *codeEditor;
    QPlainTextEdit *outputConsole;
    QPushButton *runButton;
};