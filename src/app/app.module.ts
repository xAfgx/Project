import { NgModule } from "@angular/core";
import { BrowserModule } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { AppComponent } from "./app.component";
import { DropSetupsComponent } from "./drop-setups/drop-setups.component";
import { MonitorRuntimePreloadComponent } from "./monitor-runtime-preload/monitor-runtime-preload.component";
import { ProfilePaymentComponent } from "./profile-payment/profile-payment.component";
import { ProfileCookieSnapshotsComponent } from "./profile-cookie-snapshots/profile-cookie-snapshots.component";
import { RuntimeControlComponent } from "./runtime-control/runtime-control.component";
import { RuntimeDebugConsoleComponent } from "./runtime-debug-console/runtime-debug-console.component";

@NgModule({
  declarations: [
    AppComponent,
    RuntimeControlComponent,
    RuntimeDebugConsoleComponent,
    DropSetupsComponent,
    MonitorRuntimePreloadComponent,
    ProfilePaymentComponent,
    ProfileCookieSnapshotsComponent
  ],
  imports: [BrowserModule, FormsModule],
  providers: [],
  bootstrap: [AppComponent, RuntimeDebugConsoleComponent]
})
export class AppModule {}
