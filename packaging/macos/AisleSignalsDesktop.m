#import <Cocoa/Cocoa.h>
#import <ServiceManagement/ServiceManagement.h>
#import <WebKit/WebKit.h>
#include <fcntl.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static NSString *ASCloudURL(void) {
    NSString *configured = NSBundle.mainBundle.infoDictionary[@"AisleSignalsCloudOrigin"];
    return configured.length ? configured : @"https://invalid.invalid/";
}

@interface ASAppDelegate : NSObject <NSApplicationDelegate, WKNavigationDelegate>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSSegmentedControl *modeControl;
@property(nonatomic, strong) NSTask *backend;
@property(nonatomic, strong) NSFileHandle *logHandle;
@property(nonatomic, strong) NSMenuItem *startAtLoginItem;
@property(nonatomic) NSInteger loadAttempts;
@property(nonatomic) BOOL applicationLoaded;
@property(nonatomic) BOOL terminating;
@property(nonatomic) NSInteger localPort;
@property(nonatomic, copy) NSString *localURL;
@end

@implementation ASAppDelegate

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [self configureApplicationMenu];
    self.localPort = [self selectLocalPort];
    self.localURL = [NSString stringWithFormat:@"http://127.0.0.1:%ld/#live-detection", (long)self.localPort];
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;

    NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
        NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1320, 840)
                                              styleMask:style
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"AisleSignals";
    self.window.minSize = NSMakeSize(980, 640);

    NSView *root = [[NSView alloc] initWithFrame:NSZeroRect];
    self.modeControl = [NSSegmentedControl segmentedControlWithLabels:@[@"Cloud workspace", @"Live detection"]
                                                          trackingMode:NSSegmentSwitchTrackingSelectOne
                                                                target:self
                                                                action:@selector(modeChanged:)];
    self.modeControl.selectedSegment = 0;
    self.modeControl.segmentStyle = NSSegmentStyleRounded;
    self.modeControl.translatesAutoresizingMaskIntoConstraints = NO;
    self.webView.translatesAutoresizingMaskIntoConstraints = NO;
    [root addSubview:self.modeControl];
    [root addSubview:self.webView];
    [NSLayoutConstraint activateConstraints:@[
        [self.modeControl.topAnchor constraintEqualToAnchor:root.topAnchor constant:12],
        [self.modeControl.leadingAnchor constraintEqualToAnchor:root.leadingAnchor constant:16],
        [self.webView.topAnchor constraintEqualToAnchor:self.modeControl.bottomAnchor constant:10],
        [self.webView.leadingAnchor constraintEqualToAnchor:root.leadingAnchor],
        [self.webView.trailingAnchor constraintEqualToAnchor:root.trailingAnchor],
        [self.webView.bottomAnchor constraintEqualToAnchor:root.bottomAnchor],
    ]];
    self.window.contentView = root;
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
    [self performSelector:@selector(loadCloudWorkspace) withObject:nil afterDelay:0.2];
}

- (void)configureApplicationMenu {
    NSMenu *main = [[NSMenu alloc] initWithTitle:@""];
    NSMenuItem *applicationItem = [[NSMenuItem alloc] initWithTitle:@"AisleSignals Pilot" action:nil keyEquivalent:@""];
    NSMenu *applicationMenu = [[NSMenu alloc] initWithTitle:@"AisleSignals Pilot"];
    self.startAtLoginItem = [[NSMenuItem alloc] initWithTitle:@"Start at Login"
                                                      action:@selector(toggleStartAtLogin:)
                                               keyEquivalent:@""];
    self.startAtLoginItem.target = self;
    [applicationMenu addItem:self.startAtLoginItem];
    [applicationMenu addItem:NSMenuItem.separatorItem];
    [applicationMenu addItemWithTitle:@"Quit AisleSignals" action:@selector(terminate:) keyEquivalent:@"q"];
    applicationItem.submenu = applicationMenu;
    [main addItem:applicationItem];
    NSApp.mainMenu = main;
    [self refreshStartAtLoginState];
}

- (void)refreshStartAtLoginState {
    SMAppServiceStatus status = SMAppService.mainAppService.status;
    self.startAtLoginItem.state = status == SMAppServiceStatusEnabled ? NSControlStateValueOn : NSControlStateValueOff;
    self.startAtLoginItem.toolTip = status == SMAppServiceStatusRequiresApproval
        ? @"macOS requires approval in System Settings > General > Login Items." : nil;
}

- (void)toggleStartAtLogin:(id)sender {
    SMAppService *service = SMAppService.mainAppService;
    NSError *error = nil;
    BOOL succeeded = service.status == SMAppServiceStatusEnabled
        ? [service unregisterAndReturnError:&error]
        : [service registerAndReturnError:&error];
    [self refreshStartAtLoginState];
    if (succeeded && service.status != SMAppServiceStatusRequiresApproval) return;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = service.status == SMAppServiceStatusRequiresApproval
        ? @"Approve AisleSignals in Login Items"
        : @"Start at Login could not be changed";
    alert.informativeText = service.status == SMAppServiceStatusRequiresApproval
        ? @"Open System Settings > General > Login Items and allow AisleSignals. This setting starts the app; monitoring still requires staff to select the CCTV source and arm detection."
        : (error.localizedDescription ?: @"The setting was left unchanged.");
    [alert addButtonWithTitle:@"OK"];
    [alert runModal];
}

- (void)modeChanged:(NSSegmentedControl *)sender {
    self.applicationLoaded = NO;
    self.loadAttempts = 0;
    if (sender.selectedSegment == 0) {
        [self loadCloudWorkspace];
    } else {
        [self loadLocalApplication];
    }
}

- (void)loadCloudWorkspace {
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:ASCloudURL()]
                                               cachePolicy:NSURLRequestUseProtocolCachePolicy
                                           timeoutInterval:15.0]];
}

- (BOOL)allowedTopLevelURL:(NSURL *)url {
    if ([url.scheme isEqualToString:@"about"]) return YES;
    NSURLComponents *candidate = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO];
    NSURLComponents *local = [NSURLComponents componentsWithString:self.localURL];
    NSURLComponents *cloud = [NSURLComponents componentsWithString:ASCloudURL()];
    if (candidate.user.length || candidate.password.length) return NO;
    BOOL localMatch = [candidate.scheme isEqualToString:local.scheme] &&
        [candidate.host.lowercaseString isEqualToString:local.host.lowercaseString] &&
        candidate.port.integerValue == local.port.integerValue;
    BOOL cloudMatch = [candidate.scheme isEqualToString:@"https"] &&
        [candidate.host.lowercaseString isEqualToString:cloud.host.lowercaseString] &&
        candidate.port.integerValue == cloud.port.integerValue;
    return localMatch || cloudMatch;
}

- (NSInteger)selectLocalPort {
    // Reserve-probe a private high port. NSTask cannot inherit a listening
    // socket, so the child still owns the authoritative bind. If another
    // process wins the short handoff race, child termination fails the window
    // closed instead of adopting that process.
    for (NSInteger attempt = 0; attempt < 64; attempt++) {
        int descriptor = socket(AF_INET, SOCK_STREAM, 0);
        if (descriptor < 0) continue;
        struct sockaddr_in address = {0};
        address.sin_len = sizeof(address);
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        NSInteger port = 18000 + arc4random_uniform(28000);
        address.sin_port = htons((uint16_t)port);
        int result = bind(descriptor, (struct sockaddr *)&address, sizeof(address));
        close(descriptor);
        if (result == 0) return port;
    }
    return 0;
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action
    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    // Subresources retain WebKit's same-origin/CSP controls. A top-level link,
    // redirect or target=_blank must never turn the authenticated desktop
    // window into an arbitrary credential-bearing browser.
    if (action.targetFrame == nil || (action.targetFrame.mainFrame && ![self allowedTopLevelURL:action.request.URL])) {
        decisionHandler(WKNavigationActionPolicyCancel);
        if (!self.terminating) [self showFailure:@"AisleSignals blocked navigation outside its configured local and cloud workspaces."];
        return;
    }
    decisionHandler(WKNavigationActionPolicyAllow);
}

- (NSFileHandle *)privateLogHandle:(NSError **)error {
    NSURL *support = [[NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory
                                                            inDomains:NSUserDomainMask] firstObject];
    NSURL *directory = [support URLByAppendingPathComponent:@"AisleSignals" isDirectory:YES];
    NSFileManager *manager = NSFileManager.defaultManager;
    if (![manager createDirectoryAtURL:directory withIntermediateDirectories:YES
                            attributes:@{NSFilePosixPermissions: @0700} error:error]) return nil;
    struct stat directoryInfo;
    if (lstat(directory.fileSystemRepresentation, &directoryInfo) != 0 || !S_ISDIR(directoryInfo.st_mode) ||
        S_ISLNK(directoryInfo.st_mode) || directoryInfo.st_uid != getuid() || chmod(directory.fileSystemRepresentation, 0700) != 0) {
        if (error) *error = [NSError errorWithDomain:@"ie.aislesignals.desktop" code:1
            userInfo:@{NSLocalizedDescriptionKey: @"The private log directory is unsafe."}];
        return nil;
    }
    NSURL *logURL = [directory URLByAppendingPathComponent:@"desktop.log"];
    int descriptor = open(logURL.fileSystemRepresentation, O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW, 0600);
    if (descriptor < 0 || fchmod(descriptor, 0600) != 0) {
        if (descriptor >= 0) close(descriptor);
        if (error) *error = [NSError errorWithDomain:@"ie.aislesignals.desktop" code:2
            userInfo:@{NSLocalizedDescriptionKey: @"The private desktop log could not be opened."}];
        return nil;
    }
    return [[NSFileHandle alloc] initWithFileDescriptor:descriptor closeOnDealloc:YES];
}

- (void)startBackend {
    if (self.localPort == 0) {
        [self showFailure:@"AisleSignals could not reserve a private local service port."];
        return;
    }
    NSURL *resources = NSBundle.mainBundle.resourceURL;
    NSURL *executable = [[resources URLByAppendingPathComponent:@"AisleSignalsPilot"]
        URLByAppendingPathComponent:@"AisleSignalsPilot"];
    NSError *error = nil;
    self.logHandle = [self privateLogHandle:&error];
    if (!self.logHandle) {
        [self showFailure:[NSString stringWithFormat:@"AisleSignals could not create its private log. %@", error.localizedDescription]];
        return;
    }

    self.backend = [[NSTask alloc] init];
    self.backend.executableURL = executable;
    // The launcher itself validates the pinned runtime and model digests. If
    // they are absent or invalid it starts the API with vision explicitly
    // disabled; when they are valid it starts and health-checks the model
    // before the API. The desktop wrapper must not force either outcome.
    self.backend.arguments = @[@"--no-browser", @"--port",
                               [NSString stringWithFormat:@"%ld", (long)self.localPort], @"--owner-pid",
                               [NSString stringWithFormat:@"%d", getpid()]];
    self.backend.standardOutput = self.logHandle;
    self.backend.standardError = self.logHandle;
    __weak ASAppDelegate *weakSelf = self;
    self.backend.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            ASAppDelegate *owner = weakSelf;
            if (owner && !owner.terminating) {
                [owner showFailure:[NSString stringWithFormat:
                    @"The secure local service stopped unexpectedly (status %d). Close AisleSignals before retrying; it did not adopt another service on this port.",
                    task.terminationStatus]];
            }
        });
    };
    if (![self.backend launchAndReturnError:&error]) {
        [self showFailure:[NSString stringWithFormat:@"AisleSignals could not start. %@", error.localizedDescription]];
    }
}

- (void)loadLocalApplication {
    if (!self.backend.running) {
        [self showFailure:@"The secure local service is not running. Close AisleSignals before retrying; an occupied port is never adopted."];
        return;
    }
    self.loadAttempts += 1;
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:self.localURL]
                                               cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                           timeoutInterval:2.0]];
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    self.applicationLoaded = YES;
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (self.modeControl.selectedSegment == 1 && !self.applicationLoaded && self.loadAttempts < 80) {
        [self performSelector:@selector(loadLocalApplication) withObject:nil afterDelay:0.5];
    } else if (!self.applicationLoaded && self.modeControl.selectedSegment == 1) {
        [self showFailure:@"Local monitoring did not become ready. Close the app and try again. Details are in ~/Library/Application Support/AisleSignals/desktop.log."];
    } else if (!self.applicationLoaded) {
        [self showFailure:@"The cloud workspace could not be reached. Check this laptop's internet connection and try again."];
    }
}

- (void)showFailure:(NSString *)message {
    NSLog(@"AisleSignals desktop failure: %@", message);
    NSString *escaped = [[[message stringByReplacingOccurrencesOfString:@"&" withString:@"&amp;"]
        stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"]
        stringByReplacingOccurrencesOfString:@">" withString:@"&gt;"];
    NSString *html = [NSString stringWithFormat:@"<body style='background:#071827;color:#edf8f5;font:16px -apple-system;padding:56px'><h2>Unable to start AisleSignals</h2><p>%@</p></body>", escaped];
    [self.webView loadHTMLString:html baseURL:nil];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.terminating = YES;
    if (self.backend.running) {
        [self.backend terminate];
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:6.0];
        while (self.backend.running && deadline.timeIntervalSinceNow > 0) {
            [NSThread sleepForTimeInterval:0.05];
        }
        if (self.backend.running) kill(self.backend.processIdentifier, SIGKILL);
        [self.backend waitUntilExit];
    }
    [self.logHandle closeFile];
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
