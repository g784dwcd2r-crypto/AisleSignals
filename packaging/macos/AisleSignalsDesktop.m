#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static NSString *const ASApplicationURL = @"http://127.0.0.1:8765/#live-detection";

@interface ASAppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSTask *backend;
@property(nonatomic) NSInteger loadAttempts;
@property(nonatomic) BOOL applicationLoaded;
@end

@implementation ASAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;

    NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
        NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1320, 840)
                                              styleMask:style
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"AisleSignals";
    self.window.titlebarAppearsTransparent = YES;
    self.window.minSize = NSMakeSize(980, 640);
    self.window.contentView = self.webView;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];

    NSString *starting = @"<!doctype html><html><head><meta name='color-scheme' content='dark'>"
        "<style>body{margin:0;background:#071827;color:#edf8f5;font:16px -apple-system,sans-serif;display:grid;place-items:center;height:100vh}"
        ".card{text-align:center}.mark{width:64px;height:64px;border-radius:18px;background:#11b9a5;margin:0 auto 22px;display:grid;place-items:center;color:#071827;font-size:32px;font-weight:800}"
        ".sub{color:#9ab6b0;margin-top:8px}</style></head><body><div class='card'><div class='mark'>A</div>"
        "<strong>Starting AisleSignals</strong><div class='sub'>Preparing secure local monitoring…</div></div></body></html>";
    [self.webView loadHTMLString:starting baseURL:nil];
    [self startBackend];
    [self performSelector:@selector(loadApplication) withObject:nil afterDelay:0.6];
}

- (void)startBackend {
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *executable = [[resources URLByAppendingPathComponent:@"AisleSignalsPilot"]
        URLByAppendingPathComponent:@"AisleSignalsPilot"];
    NSURL *support = [[NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory
                                                            inDomains:NSUserDomainMask] firstObject];
    NSURL *logs = [support URLByAppendingPathComponent:@"AisleSignals" isDirectory:YES];
    [NSFileManager.defaultManager createDirectoryAtURL:logs withIntermediateDirectories:YES attributes:nil error:nil];
    NSURL *logURL = [logs URLByAppendingPathComponent:@"desktop.log"];
    if (![NSFileManager.defaultManager fileExistsAtPath:logURL.path]) {
        [NSFileManager.defaultManager createFileAtPath:logURL.path contents:nil attributes:nil];
    }
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:logURL.path];
    [log seekToEndOfFile];

    self.backend = [[NSTask alloc] init];
    self.backend.executableURL = executable;
    self.backend.arguments = @[@"--casework-only", @"--no-browser"];
    self.backend.standardOutput = log;
    self.backend.standardError = log;
    NSError *error = nil;
    if (![self.backend launchAndReturnError:&error]) {
        [self showFailure:[NSString stringWithFormat:@"AisleSignals could not start. %@", error.localizedDescription]];
    }
}

- (void)loadApplication {
    self.loadAttempts += 1;
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:ASApplicationURL]
                                               cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                           timeoutInterval:2.0]];
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    if ([webView.URL.host isEqualToString:@"127.0.0.1"]) self.applicationLoaded = YES;
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (!self.applicationLoaded && self.loadAttempts < 80) {
        [self performSelector:@selector(loadApplication) withObject:nil afterDelay:0.5];
    } else if (!self.applicationLoaded) {
        [self showFailure:@"AisleSignals did not become ready. Close the app and try again. Details are in ~/Library/Application Support/AisleSignals/desktop.log."];
    }
}

- (void)showFailure:(NSString *)message {
    NSString *escaped = [[[message stringByReplacingOccurrencesOfString:@"&" withString:@"&amp;"]
        stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"]
        stringByReplacingOccurrencesOfString:@">" withString:@"&gt;"];
    NSString *html = [NSString stringWithFormat:@"<body style='background:#071827;color:#edf8f5;font:16px -apple-system;padding:56px'><h2>Unable to start AisleSignals</h2><p>%@</p></body>", escaped];
    [self.webView loadHTMLString:html baseURL:nil];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }

- (void)applicationWillTerminate:(NSNotification *)notification {
    if (self.backend.running) {
        [self.backend terminate];
        [self.backend waitUntilExit];
    }
}

@end

static int runCommandLineMode(NSArray<NSString *> *arguments) {
    NSURL *executable = [[NSBundle.mainBundle.resourceURL URLByAppendingPathComponent:@"AisleSignalsPilot"]
        URLByAppendingPathComponent:@"AisleSignalsPilot"];
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = executable;
    task.arguments = arguments;
    task.standardInput = [NSFileHandle fileHandleWithStandardInput];
    task.standardOutput = [NSFileHandle fileHandleWithStandardOutput];
    task.standardError = [NSFileHandle fileHandleWithStandardError];
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        fprintf(stderr, "AisleSignals could not start: %s\n", error.localizedDescription.UTF8String);
        return 1;
    }
    [task waitUntilExit];
    return task.terminationStatus;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc > 1) {
            NSMutableArray<NSString *> *arguments = [NSMutableArray array];
            for (int i = 1; i < argc; i++) [arguments addObject:[NSString stringWithUTF8String:argv[i]]];
            return runCommandLineMode(arguments);
        }
        NSApplication *application = NSApplication.sharedApplication;
        ASAppDelegate *delegate = [[ASAppDelegate alloc] init];
        application.delegate = delegate;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        [application run];
    }
    return 0;
}
