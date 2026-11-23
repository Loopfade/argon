"""Run Chromium's real duplicate creator and per-tab UA selection methods."""
from pathlib import Path
import tempfile

from apply_homepage import method_source
from test_user_agent import compile_java, run_java

FIXTURES = Path(__file__).resolve().parent / 'fixtures/user_agent'
CREATOR_SIGNATURE = '    public @Nullable Tab createTabWithWebContents('
BUILDER_API = Path('chrome/android/java/src/org/chromium/chrome/browser/tab/TabBuilder.java')
STATE_SIGNATURES = (
    '    public void setUserAgent(@TabUserAgent int userAgent) {',
    '    @UserAgentOverrideOption int calculateUserAgentOverrideOption(',
    '    private void switchUserAgentIfNeeded() {',
)

ADAPTERS = '''
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;
@interface Nullable {}
@interface JniType { String value(); }
@interface TabUserAgent {int DEFAULT=0,MOBILE=1,DESKTOP=2;}
@interface UserAgentOverrideOption {int INHERIT=0,TRUE=1,FALSE=2;}
@interface TabLaunchType {int FROM_TAB_LIST_INTERFACE=1,FROM_CHROME_UI=2;}
@interface TabCreationState {int FROZEN_FOR_LAZY_LOAD=0,LIVE_IN_FOREGROUND=1,LIVE_IN_BACKGROUND=2;}
class GURL {static boolean isEmptyOrInvalid(GURL url){return url==null;}}
class Profile {static Profile fromWebContents(WebContents web){return new Profile();}}
class Frame {boolean live;boolean isRenderFrameLive(){return live;}}
class WebContents {
 Frame frame=new Frame();NavigationController navigation=new NavigationController();
 Frame getMainFrame(){return frame;}GURL getVisibleUrl(){return new GURL();}
 NavigationController getNavigationController(){return navigation;}
}
class NavigationController {boolean desktop;boolean getUseDesktopUserAgent(){return desktop;}}
class DesktopSiteUtils {static boolean rule;static boolean shouldOverrideDesktopSite(Object p,GURL u,Object c){return rule;}}
class TabUtils {
 static boolean isUsingDesktopUserAgent(WebContents web){return web.navigation.desktop;}
 static void switchUserAgent(Tab tab,boolean desktop,boolean explicit){throw new AssertionError("unexpected reload");}
}
class TabStateAttributes {static int dirty;static void setDirty(Tab tab){dirty++;}}
class Tab {
 static final int INVALID_TAB_ID=-1;int mUserAgent=TabUserAgent.DEFAULT;WebContents web;
 int getId(){return 1;}int getUserAgent(){return mUserAgent;}
 void setUserAgent(int mode){mUserAgent=mode;}
}
class TabImpl extends Tab {
 Object mProfile=new Object();WebContents getWebContents(){return web;}
 Object getContext(){return new Object();}boolean isClosing(){return false;}
 boolean isDestroyed(){return false;}void recordHistogramUseDesktopUserAgent(boolean mode){}
@STATE_METHODS@
}
class TabBuilder {
 WebContents web;Consumer<Tab> before;
 static TabBuilder createLazyTabWithWebContents(Profile p){return new TabBuilder();}
 static TabBuilder createLiveTab(Profile p,boolean hidden){return new TabBuilder();}
 TabBuilder setParent(Tab p){return this;}TabBuilder setWindow(Object w){return this;}
 TabBuilder setLaunchType(int t){return this;}TabBuilder setWebContents(WebContents v){web=v;return this;}
 TabBuilder setDelegateFactory(Object d){return this;}TabBuilder setInitiallyHidden(boolean h){return this;}
 TabBuilder setInitialPinState(boolean p){return this;}
 TabBuilder setPreInitializeAction(Consumer<Tab> callback){before=callback;return this;}
 Tab build(){
  TabImpl tab=new TabImpl();tab.web=web;if(before!=null)before.accept(tab);
  // This is the boundary at which real TabImpl.initWebContents() reads its mode.
  int choice=tab.calculateUserAgentOverrideOption(null);
  if(choice!=UserAgentOverrideOption.INHERIT)web.navigation.desktop=choice==UserAgentOverrideOption.TRUE;
  return tab;
 }
}
class TraceEvent implements AutoCloseable {static TraceEvent scoped(String n){return new TraceEvent();}public void close(){}}
class TabDelegateFactory {}
class Model {boolean isClosurePending(int id){return false;}void addTab(Tab t,int p,int type,int state){}}
class Order {boolean willOpenInForeground(int type,boolean incognito){return true;}}
public class CreatorFlow {
 static <T> T assumeNonNull(T value){return java.util.Objects.requireNonNull(value);}
 Model mTabModel=new Model();Order mOrderController=new Order();Object mNativeWindow;
 boolean mIncognito;int evaluateNewTabPosition(int p,int id){return p;}
 TabDelegateFactory createDefaultTabDelegateFactory(){return new TabDelegateFactory();}
@CREATOR_METHOD@
 public static void main(String[] args){
  for(boolean desktop:new boolean[]{false,true})for(boolean live:new boolean[]{false,true}){
   Tab parent=new Tab();parent.setUserAgent(desktop?TabUserAgent.DESKTOP:TabUserAgent.MOBILE);
   DesktopSiteUtils.rule=!desktop;
   for(int type:new int[]{TabLaunchType.FROM_TAB_LIST_INTERFACE,TabLaunchType.FROM_CHROME_UI}){
    WebContents copy=new WebContents();copy.navigation.desktop=desktop;copy.frame.live=live;
    Tab actual=new CreatorFlow().createTabWithWebContents(parent,false,copy,type,-1,CompletableFuture.completedFuture(true));
    boolean duplicate=type==TabLaunchType.FROM_TAB_LIST_INTERFACE;
    if(actual.getUserAgent()!=(duplicate?parent.getUserAgent():TabUserAgent.DEFAULT))throw new AssertionError("manual choice lost/leaked");
    if(copy.navigation.desktop!=(duplicate?desktop:!desktop))throw new AssertionError("wrong first-load UA");
    if(parent.getUserAgent()!=(desktop?TabUserAgent.DESKTOP:TabUserAgent.MOBILE))throw new AssertionError("parent changed");
   }
  }
  TabImpl tab=new TabImpl();TabStateAttributes.dirty=0;
  tab.setUserAgent(TabUserAgent.MOBILE);tab.setUserAgent(TabUserAgent.DEFAULT);
  if(TabStateAttributes.dirty!=2)throw new AssertionError("manual/reset choice not persisted");
  tab.setUserAgent(TabUserAgent.DEFAULT);
  if(TabStateAttributes.dirty!=2)throw new AssertionError("unchanged choice dirtied");
  System.out.println("PASS: duplicate first-load UA, ordinary child isolation and persisted automatic reset");
 }
}
'''


def run_creator(creator, tab):
    state = '\n'.join(method_source(tab, signature) for signature in STATE_SIGNATURES)
    program = ADAPTERS.replace('@STATE_METHODS@', state).replace(
        '@CREATOR_METHOD@', method_source(creator, CREATOR_SIGNATURE))
    with tempfile.TemporaryDirectory(prefix='argon-ua-duplicate-') as directory:
        root = Path(directory)
        compile_java(root, [], {'CreatorFlow.java': program})
        return run_java(root, 'CreatorFlow')


def verify_upstream(src):
    import apply_user_agent as patch
    builder = (src / BUILDER_API).read_text()
    build = method_source(builder, '    public Tab build() {')
    if build.index('mPreInitializeAction.onResult(tab)') >= build.index('tab.initialize('):
        raise ValueError('Pinned tab builder must set the initial UA before WebContents initialization')
    creator = (src / patch.TAB_CREATOR).read_text()
    expected = patch.patch_tab_creator((FIXTURES / 'tab_creator.java.in').read_text())
    if method_source(creator, CREATOR_SIGNATURE) != method_source(expected, CREATOR_SIGNATURE):
        raise ValueError('Pinned duplicate tab creator changed')
    tab = (src / patch.TAB_IMPL).read_text()
    expected = patch.patch_tab_user_agent_state((FIXTURES / 'tab_user_agent_state.java.in').read_text())
    for signature in STATE_SIGNATURES:
        if method_source(tab, signature) != method_source(expected, signature):
            raise ValueError('Pinned per-tab UA state changed')
    return run_creator(creator, tab).strip()
