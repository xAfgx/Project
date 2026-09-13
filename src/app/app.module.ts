import { NgModule } from "@angular/core";
import { BrowserModule } from "@angular/platform-browser";
import { FormsModule } from "@angular/forms";
import { AppComponent } from "./app.component";
import { DropSetupsComponent } from "./drop-setups/drop-setups.component";
import { MonitorRuntimePreloadComponent } from "./monitor-runtime-preload/monitor-runtime-preload.component";
import { ProfilePaymentComponent } from "./profile-payment/profile-payment.component";
import { ProfileCookieSnapshotsComponent } from "./profile-cookie-snapshots/profile-cookie-snapshots.component";
import { RuntimeControlComponent } from "./runtime-control/runtime-control.component";
import { TranslatePipe } from "./i18n/translate.pipe";

@NgModule({
  declarations: [
    AppComponent,
    RuntimeControlComponent,
    DropSetupsComponent,
    MonitorRuntimePreloadComponent,
    ProfilePaymentComponent,
    ProfileCookieSnapshotsComponent,
    TranslatePipe
  ],
  imports: [BrowserModule, FormsModule],
  providers: [],
  bootstrap: [AppComponent]
})
export class AppModule {}